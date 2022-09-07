
import sys
import torch
import math
from gdl.models.temporal.Bases import TemporalAudioEncoder
from gdl.utils.other import get_path_to_externals
path_to_av_hubert = get_path_to_externals() / "av_hubert"
# path_to_av_hubert = get_path_to_externals() / "av_hubert" / "avhubert"
# path_to_fairseq = get_path_to_externals() / "av_hubert" / "fairseq"
if str(path_to_av_hubert) not in sys.path:
    sys.path.insert(0, str(path_to_av_hubert))
import avhubert


class AvHubertAudioEncoder(TemporalAudioEncoder):

    def __init__(self, avhubert_model, trainable):
        super().__init__()
        assert isinstance(avhubert_model, avhubert.AVHubertSeq2Seq)
        self.avhubert = avhubert_model.encoder.w2v_model 
        del self.avhubert.feature_extractor_video 
        del self.avhubert.encoder # the transformed encoder will not be used here
        del self.avhubert.final_proj # figure out what to do with this one
        self.trainable = trainable

    def forward(self, sample, train=False): 
        audio = sample["audio"]
        # B = audio.shape[0]
        # T = audio.shape[1] 
        # audio = audio.view(B * T, -1)
        audio_in = audio.transpose(1, 2)
        if self.trainable:
            features = self.avhubert.forward_features(audio_in, modality="audio")
        else: 
            with torch.no_grad(): 
                features = self.avhubert.forward_features(audio_in, modality="audio")
        # features = features.view(B, T, -1)
        features = features.transpose(1, 2)                
        sample["audio_feature"] = features
        return sample

    def train(self, mode: bool = True):
        # return super().train(mode)
        mode = mode and self.trainable 
        self.avhubert.train(mode)
        return self

    def get_trainable_parameters(self): 
        if self.trainable:
            return list(self.avhubert.parameters())
        return []

    def output_feature_dim(self):
        return self.avhubert.feature_extractor_audio.proj.out_features



from transformers import Wav2Vec2Model, Wav2Vec2Processor, Wav2Vec2Config 
from transformers.models.wav2vec2.modeling_wav2vec2 import _compute_mask_indices, BaseModelOutput, Wav2Vec2BaseModelOutput
import torch.nn.functional as F


def temporal_interpolation(features, input_fps, output_fps, output_len=None):
    features = features.transpose(1, 2)
    seq_len = features.shape[2] / float(input_fps)
    if output_len is None:
        # output_len = int(math.ceil(seq_len * output_fps))
        output_len = int(math.ceil(seq_len) * output_fps)
    output_features = F.interpolate(features,size=output_len,align_corners=True,mode='linear')
    return output_features.transpose(1, 2)


class Wav2Vec2ModelResampled(Wav2Vec2Model):

    """
    Wav2vec2 model with temporal resampling after the self.feature_extractor step. Everything else is identical to the base class.
    """

    def __init__(self, config, target_fps = 25, model_expected_fps = 50):
        super().__init__(config)
        self.model_expected_fps = model_expected_fps # default is 50 (at least for pretrained "facebook/wav2vec2-base-960h")
        self.target_fps = target_fps # default is 25 because that is what LRS3 dataset framerate is

    # def forward(
    #     self,
    #     input_values,
    #     attention_mask=None,
    #     output_attentions=None,
    #     output_hidden_states=None,
    #     return_dict=None,
    #     num_output_frames=None
    # ):
    #     self.config.output_attentions = True
    #     output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
    #     output_hidden_states = (
    #         output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
    #     )
    #     return_dict = return_dict if return_dict is not None else self.config.use_return_dict

    #     hidden_states = self.feature_extractor(input_values)
    #     hidden_states = hidden_states.transpose(1, 2)

    #     ## This is where we do the temporal resampling, if necessary. It is the only difference to the base class.
    #     if self.model_expected_fps != self.target_fps or num_output_frames is not None:
    #         hidden_states = temporal_interpolation(hidden_states, self.model_expected_fps, self.target_fps, output_len=num_output_frames)
    #     ## End of temporal resampling. From here on, everything is identical to the base class.

    #     if attention_mask is not None:
    #         output_lengths = self._get_feat_extract_output_lengths(attention_mask.sum(-1))
    #         attention_mask = torch.zeros(
    #             hidden_states.shape[:2], dtype=hidden_states.dtype, device=hidden_states.device
    #         )
    #         attention_mask[
    #             (torch.arange(attention_mask.shape[0], device=hidden_states.device), output_lengths - 1)
    #         ] = 1
    #         attention_mask = attention_mask.flip([-1]).cumsum(-1).flip([-1]).bool()

    #     hidden_states, extract_features = self.feature_projection(hidden_states)

    #     if self.config.apply_spec_augment and self.training:
    #         batch_size, sequence_length, hidden_size = hidden_states.size()
    #         if self.config.mask_time_prob > 0:
    #             mask_time_indices = _compute_mask_indices(
    #                 (batch_size, sequence_length),
    #                 self.config.mask_time_prob,
    #                 self.config.mask_time_length,
    #                 attention_mask=attention_mask,
    #                 min_masks=2,
    #             )
    #             hidden_states[torch.from_numpy(mask_time_indices)] = self.masked_spec_embed.to(hidden_states.dtype)
    #         if self.config.mask_feature_prob > 0:
    #             mask_feature_indices = _compute_mask_indices(
    #                 (batch_size, hidden_size),
    #                 self.config.mask_feature_prob,
    #                 self.config.mask_feature_length,
    #             )
    #             mask_feature_indices = torch.from_numpy(mask_feature_indices).to(hidden_states.device)
    #             hidden_states[mask_feature_indices[:, None].expand(-1, sequence_length, -1)] = 0
    #     encoder_outputs = self.encoder(
    #         hidden_states,
    #         attention_mask=attention_mask,
    #         output_attentions=output_attentions,
    #         output_hidden_states=output_hidden_states,
    #         return_dict=return_dict,
    #     )
    #     hidden_states = encoder_outputs[0]
    #     if not return_dict:
    #         return (hidden_states,) + encoder_outputs[1:]

    #     return BaseModelOutput(
    #         last_hidden_state=hidden_states,
    #         hidden_states=encoder_outputs.hidden_states,
    #         attentions=encoder_outputs.attentions,
    #     )

    def forward(
        self,
        input_values,
        attention_mask=None,
        mask_time_indices=None,
        output_attentions=None,
        output_hidden_states=None,
        return_dict=None,
        num_output_frames=None
    ):

        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
        output_hidden_states = (
            output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        )
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict

        extract_features = self.feature_extractor(input_values)
        extract_features = extract_features.transpose(1, 2)

        ## This is where we do the temporal resampling, if necessary. It is the only difference to the base class.
        if self.model_expected_fps != self.target_fps or num_output_frames is not None:
            extract_features = temporal_interpolation(extract_features, self.model_expected_fps, self.target_fps, output_len=num_output_frames)
        ## End of temporal resampling. From here on, everything is identical to the base class.

        if attention_mask is not None:
            # compute reduced attention_mask corresponding to feature vectors
            attention_mask = self._get_feature_vector_attention_mask(extract_features.shape[1], attention_mask)

        hidden_states, extract_features = self.feature_projection(extract_features)
        hidden_states = self._mask_hidden_states(
            hidden_states, mask_time_indices=mask_time_indices, attention_mask=attention_mask
        )

        encoder_outputs = self.encoder(
            hidden_states,
            attention_mask=attention_mask,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
        )

        hidden_states = encoder_outputs[0]

        if not return_dict:
            return (hidden_states, extract_features) + encoder_outputs[1:]

        return Wav2Vec2BaseModelOutput(
            last_hidden_state=hidden_states,
            extract_features=extract_features,
            hidden_states=encoder_outputs.hidden_states,
            attentions=encoder_outputs.attentions,
        )


class Wav2Vec2Encoder(TemporalAudioEncoder):

    def __init__(self, model_specifier, trainable, with_processor=True, target_fps=25, expected_fps=50):
        super().__init__() 
        self.model_specifier = model_specifier
        self.cfg  =  Wav2Vec2Config.from_pretrained(model_specifier)
        if with_processor:
            self.input_processor = Wav2Vec2Processor.from_pretrained(model_specifier)
        else: 
            self.input_processor = None
        # self.model = Wav2Vec2Model.from_pretrained(model_specifier)
        if not target_fps or not expected_fps:
            self.model = Wav2Vec2Model.from_pretrained(model_specifier)
            self.resampling = False
        else:
            self.model = Wav2Vec2ModelResampled.from_pretrained(model_specifier)
            self.resampling = True
            self.model.model_expected_fps = expected_fps
            self.model.target_fps = target_fps
        self.trainable = trainable
        if not trainable: 
            self.model.requires_grad_(False)
            self.model.feature_extractor._freeze_parameters()

    def get_trainable_parameters(self): 
        if self.trainable:
            return list(self.model.parameters())
        return []

    def _forward(self, sample, train=False): 
        if self.input_processor is not None:
            B = sample["raw_audio"].shape[0]
            T = sample["raw_audio"].shape[1]
            # proc = self.input_processor(sample["raw_audio"], sampling_rate=sample["samplerate"], return_tensors="pt")[0]
            # raw_audio = sample["raw_audio"].view( B, -1)
            raw_audio = sample["raw_audio"].view( B, -1)
            proc = self.input_processor(raw_audio, sampling_rate=sample["samplerate"][0], return_tensors="pt")
            input = proc.input_values[0].to(device=raw_audio.device)
            sample["processed_audio"] = input
        else: 
            B = sample["processed_audio"].shape[0]
            # T = sample["processed_audio"].shape[1]
            T = None
            input = sample["processed_audio"]
        if isinstance(self.model, Wav2Vec2ModelResampled):
            feats_ = self.model(input, num_output_frames=T)
            # feats_ = self.model(input)
        else:
            feats_ = self.model(input)
        F = feats_.last_hidden_state.shape[-1]
        T2 = feats_.last_hidden_state.shape[1]

        if self.resampling and T is not None:
            assert T2 == T # sanity checking that the feature got resampled to the proper length

        sample["audio_feature"] = feats_.last_hidden_state 
        return sample

        # assert T2 + 1  == 2*T # Wav2Vec doubles the feature dimensionality and then this is reduced by 1 
        # # (probably because of a temporal convolution window of 3) 

        # # feats = torch.zeros((B, T2 + 1, F),
        # #     device=feats_.last_hidden_state.device, dtype=feats_.last_hidden_state.dtype)
        # # feats[:,:T2, :] = feats_.last_hidden_state
        
        # # feats = torch.zeros((B, T2, F),
        # #     device=feats_.last_hidden_state.device, dtype=feats_.last_hidden_state.dtype)
        # # padding = torch.zeros((B, 1, F),
        # #     device=feats_.last_hidden_state.device, dtype=feats_.last_hidden_state.dtype)

        # # feats = torch.cat((feats_.last_hidden_state, padding), dim=1).contiguous()

        # # TODO: question. The sequence length seems to have doubled. Why? Should I subsample the temporal dimension (i.e. interpolate) or should I reshape?
        # # 1) reshape
        # feats = feats.view(B, T, -1)
        # # 2) subsample - dummy version, only works when T2(+1) is a multiple of T
        # # feats = feats[:,::2,:]
        # # sample["audio_feature"] = feats 
        # # return sample

    def train(self, mode: bool = True):
        # return super().train(mode)
        mode = mode and self.trainable 
        self.model.train(mode)
        return self

    def forward(self, sample, train=False): 
        if self.trainable:
            return self._forward(sample, train=train)
        else: 
            with torch.no_grad(): 
                return self._forward(sample, train=train)

    def output_feature_dim(self):
        return self.cfg.hidden_size
        # # return self.cfg.hidden_size * 2