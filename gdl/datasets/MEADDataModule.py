from pathlib import Path
from time import time
from gdl.datasets.FaceDataModuleBase import FaceDataModuleBase
from gdl.datasets.FaceVideoDataModule import FaceVideoDataModule 
import numpy as np
import torch
from gdl.datasets.ImageDatasetHelpers import bbox2point, bbpoint_warp
from gdl.transforms.imgaug import create_image_augmenter
from gdl.layers.losses.MediaPipeLandmarkLosses import MEDIAPIPE_LANDMARK_NUMBER
from gdl.utils.collate import robust_collate
from torch.utils.data import DataLoader
import subprocess

class MEADDataModule(FaceVideoDataModule): 

    def __init__(self, 
            ## begin args of FaceVideoDataModule
            root_dir, 
            output_dir, 
            processed_subfolder=None, 
            face_detector='mediapipe', 
            # landmarks_from='sr_res',
            landmarks_from=None,
            face_detector_threshold=0.5, 
            image_size=224, scale=1.25, 
            processed_video_size=384,
            batch_size_train=16,
            batch_size_val=16,
            batch_size_test=16,
            sequence_length_train=16,
            sequence_length_val=16,
            sequence_length_test=16,
            # occlusion_length_train=0,
            # occlusion_length_val=0,
            # occlusion_length_test=0,            
            bb_center_shift_x=0., # in relative numbers
            bb_center_shift_y=0., # in relative numbers (i.e. -0.1 for 10% shift upwards, ...)
            occlusion_settings_train=None,
            occlusion_settings_val=None,
            occlusion_settings_test=None,
            split = "random_70_15_15", #TODO: implement other splits if required
            num_workers=4,
            device=None,
            augmentation=None,
            drop_last=True,
            include_processed_audio = True,
            include_raw_audio = True,
            preload_videos=False,
            inflate_by_video_size=False,
            ## end args of FaceVideoDataModule
            ## begin CelebVHQDataModule specific params
            training_sampler="uniform",
            landmark_types = None,
            landmark_sources=None,
            segmentation_source=None,
            viewing_angles=None,

            ):
        super().__init__(root_dir, output_dir, processed_subfolder, 
            face_detector, face_detector_threshold, image_size, scale, 
            processed_video_size=processed_video_size,
            device=device, 
            unpack_videos=False, save_detection_images=False, 
            # save_landmarks=True,
            save_landmarks=False, # trying out this option
            save_landmarks_one_file=True, 
            save_segmentation_frame_by_frame=False, 
            save_segmentation_one_file=True,
            bb_center_shift_x=bb_center_shift_x, # in relative numbers
            bb_center_shift_y=bb_center_shift_y, # in relative numbers (i.e. -0.1 for 10% shift upwards, ...)
            include_processed_audio = include_processed_audio,
            include_raw_audio = include_raw_audio,
            preload_videos=preload_videos,
            inflate_by_video_size=inflate_by_video_size,
            )
        # self.detect_landmarks_on_restored_images = landmarks_from
        self.batch_size_train = batch_size_train
        self.batch_size_val = batch_size_val
        self.batch_size_test = batch_size_test
        self.sequence_length_train = sequence_length_train
        self.sequence_length_val = sequence_length_val
        self.sequence_length_test = sequence_length_test

        self.split = split
        self.num_workers = num_workers
        self.drop_last = drop_last

        # self.occlusion_length_train = occlusion_length_train
        # self.occlusion_length_val = occlusion_length_val
        # self.occlusion_length_test = occlusion_length_test
        self.occlusion_settings_train = occlusion_settings_train or {}
        self.occlusion_settings_val = occlusion_settings_val or {}
        self.occlusion_settings_test = occlusion_settings_test or {}
        self.augmentation = augmentation

        self.training_sampler = training_sampler.lower()
        self.annotation_json_path = None # Path(root_dir).parent / "celebvhq_info.json" 
        ## assert self.annotation_json_path.is_file()

        self.landmark_types = landmark_types or ["mediapipe", "fan"]
        self.landmark_sources = landmark_sources or ["original", "aligned"]
        self.segmentation_source = segmentation_source or "aligned"
        self.use_original_video = False

        self.viewing_angles = viewing_angles or ["front"] 
        if isinstance( self.viewing_angles, str): 
            self.viewing_angles = [self.viewing_angles]

        self._must_include_audio = True
    

    def prepare_data(self):
        # super().prepare_data()
        
        outdir = Path(self.output_dir)
        if Path(self.metadata_path).is_file():
            print("The dataset is already processed. Loading")
            self._loadMeta()
            return
        # else:
        self._gather_data()
        self._saveMeta()
        self._loadMeta()
        # self._unpack_videos()
        # self._saveMeta()

   
    def _gather_data(self, exist_ok=True):
        print(f"Processing MEAD dataset (video and audio) for angles: {self.viewing_angles} and all emotion intensities")
        Path(self.output_dir).mkdir(parents=True, exist_ok=exist_ok)

        self.video_list = [] 
        self.video_list = [] 
        for viewing_angle in self.viewing_angles:
            # video_list = sorted(list(Path(self.root_dir).rglob(f'**/video/{viewing_angle}/**/**/*.mp4')))
            # find video files using bash find command (faster than python glob)
            video_list = sorted(subprocess.check_output(f"find {self.root_dir} -wholename */{viewing_angle}/*/*/*.mp4", shell=True).decode("utf-8").splitlines())
            video_list = [Path(path).relative_to(self.root_dir) for path in video_list]
            
            self.video_list += video_list
        print("Found %d video files." % len(self.video_list))
        self._gather_video_metadata()

    # ## DOESN'T work as some videos seem to not have corresponding audios :-(
    # def _gather_data(self, exist_ok=True):
    #     print(f"Processing MEAD dataset (video and audio) for angles: {self.viewing_angles} and all emotion intensities")
    #     Path(self.output_dir).mkdir(parents=True, exist_ok=exist_ok)

    #     # find audio files using bash find command
    #     audio_list = sorted(subprocess.check_output(f"find {self.root_dir} -name '*.m4a'", shell=True).decode("utf-8").splitlines())
    #     # audio_list = sorted(Path(self.root_dir).rglob('**/audio/**/**/*.m4a'))
    #     audio_list = [Path(path).relative_to(self.root_dir) for path in audio_list]

    #     # print("videos...")
    #     # prepare video list
    #     self.video_list = [] 
    #     # self.audio_list = []
    #     for viewing_angle in self.viewing_angles:
    #         # video_list = sorted(list(Path(self.root_dir).rglob(f'**/video/{viewing_angle}/**/**/*.mp4')))

    #         # find video files using bash find command
    #         video_list = sorted(subprocess.check_output(f"find {self.root_dir} -wholename */{viewing_angle}/*/*/*.mp4", shell=True).decode("utf-8").splitlines())
    #         video_list = [Path(path).relative_to(self.root_dir) for path in video_list]
            
    #         # assert len(video_list) == len(audio_list), f"Number of videos ({len(video_list)}) and audio files ({len(audio_list)}) do not match"
            
    #         # check the audio and video names are corresponding
    #         audio_names = set([ Path("/".join((audio_list[ai].parts[-5],) + audio_list[ai].parts[-3:])).with_suffix('') for ai in range(len(audio_list)) ])
    #         for vi in range(len(video_list)):
    #             # get the name with the last two relative subfolders
    #             video_name = Path("/".join((video_list[vi].parts[-6],) + video_list[vi].parts[-3:])).with_suffix('')
    #             # audio_name = Path("/".join((audio_list[vi].parts[-5],) + audio_list[vi].parts[-3:])).with_suffix('')
    #             if video_name not in audio_names:
    #                 raise RuntimeError(f"Video {video_name} does not have corresponding audio file")
    #             # if video_name != audio_name:
    #             #     raise ValueError(f"Video name '{video_name} 'is not corresponding to audio name '{audio_name}'")


    #         self.video_list += [video_list]
    #         self.audio_list += [audio_list]

    #     # print("audios...")
    #     # prepare audio list
        
        
    #     # self._gather_video_metadata()
    #     print("Found %d video files." % len(self.video_list))


    def _filename2index(self, filename):
        return self.video_list.index(filename)

    def _get_landmark_method(self):
        return self.face_detector_type

    def _get_segmentation_method(self):
        return "bisenet"

    def _detect_faces(self):
        return super()._detect_faces( )

    def _get_num_shards(self, videos_per_shard): 
        num_shards = int(np.ceil( self.num_sequences / videos_per_shard))
        return num_shards

    def _process_video(self, idx, extract_audio=True, 
            restore_videos=True, 
            detect_landmarks=True, 
            recognize_faces=True,
            # cut_out_faces=True,
            segment_videos=True, 
            detect_aligned_landmarks=False,
            reconstruct_faces=False,):
        if extract_audio: 
            self._extract_audio_for_video(idx)
        # if restore_videos:
        #     self._deep_restore_sequence_sr_res(idx)
        if detect_landmarks:
            self._detect_faces_in_sequence(idx)
        if recognize_faces: 
            self._recognize_faces_in_sequence(idx)
            self._identify_recognitions_for_sequence(idx)
            self._extract_personal_recognition_sequences(idx)
        # if cut_out_faces: 
        #     self._cut_out_detected_faces_in_sequence(idx)
        if segment_videos:
            self._segment_faces_in_sequence(idx, use_aligned_videos=True)
            # raise NotImplementedError()
        if detect_aligned_landmarks: 
            self._detect_landmarkes_in_aligned_sequence(idx)

        if reconstruct_faces: 
            # self._reconstruct_faces_in_sequence(idx, 
            #     reconstruction_net=self._get_reconstruction_network('emoca'))
            # self._reconstruct_faces_in_sequence(idx, 
            #     reconstruction_net=self._get_reconstruction_network('deep3dface'))
            # self._reconstruct_faces_in_sequence(idx, 
            #     reconstruction_net=self._get_reconstruction_network('deca'))
            # rec_methods = ['emoca', 'deep3dface', 'deca']
            rec_methods = ['emoca', 'deep3dface',]
            # rec_methods = ['emoca',]
            for rec_method in rec_methods:
                self._reconstruct_faces_in_sequence(idx, reconstruction_net=None, device=None,
                                    save_obj=False, save_mat=True, save_vis=False, save_images=False,
                                    save_video=False, rec_method=rec_method, retarget_from=None, retarget_suffix=None)
  

    def _process_shard(self, videos_per_shard, shard_idx, extract_audio=True,
        restore_videos=True, detect_landmarks=True, segment_videos=True, 
        detect_aligned_landmarks=False,
        reconstruct_faces=False,
    ):
        num_shards = self._get_num_shards(videos_per_shard)
        start_idx = shard_idx * videos_per_shard
        end_idx = min(start_idx + videos_per_shard, self.num_sequences)

        print(f"Processing shard {shard_idx} of {num_shards}")

        idxs = np.arange(self.num_sequences, dtype=np.int32)
        np.random.seed(0)
        np.random.shuffle(idxs)

        if detect_aligned_landmarks: 
            self.face_detector_type = 'fan'
            self._instantiate_detector(overwrite=True)

        for i in range(start_idx, end_idx):
            idx = idxs[i]
            self._process_video(idx, 
                extract_audio=extract_audio, 
                restore_videos=restore_videos,
                detect_landmarks=detect_landmarks, 
                segment_videos=segment_videos, 
                detect_aligned_landmarks=detect_aligned_landmarks,
                reconstruct_faces=reconstruct_faces)
            
        print("Done processing shard")

    def _get_path_to_sequence_files(self, sequence_id, file_type, method="", suffix=""): 
        assert file_type in ['videos', 'videos_aligned', 'detections', 
            "landmarks", "landmarks_original", "landmarks_aligned",
            "segmentations", "segmentations_aligned",
            "emotions", "reconstructions", "audio"]
        video_file = self.video_list[sequence_id]
        if len(method) > 0:
            file_type += "_" + method 
        if len(suffix) > 0:
            file_type += suffix

        suffix = Path(file_type) / video_file.stem
        out_folder = Path(self.output_dir) / suffix
        return out_folder


    def _get_subsets(self, set_type=None):
        set_type = set_type or "unknown"
        self.temporal_split = None
        if "specific_video_temporal" in set_type:
            raise NotImplementedError("Not implemented yet")
        elif "random" in set_type:
            res = set_type.split("_")
            assert len(res) >= 3, "Specify the train/val/test split by 'random_train_val_test' to the set_type"
            train = int(res[1])
            val = int(res[2])
            if len(res) == 4:
                test = int(res[3])
            else:
                test = 0
            train_ = train / (train + val + test)
            val_ = val / (train + val + test)
            test_ = 1 - train_ - val_
            indices = [i for i in range(len(self.video_list))]
            import random
            seed = 4
            random.Random(seed).shuffle(indices)
            num_train = int(train_ * len(indices))
            num_val = int(val_ * len(indices))
            num_test = len(indices) - num_train - num_val

            training = indices[:num_train] 
            validation = indices[num_train:(num_train + num_val)]
            test = indices[(num_train + num_val):]
            return training, validation, test
        elif "temporal" in set_type:
            raise NotImplementedError("Not implemented yet")
        else: 
            raise ValueError(f"Unknown set type: {set_type}")


    def setup(self, stage=None):
        train, val, test = self._get_subsets(self.split)
        training_augmenter = create_image_augmenter(self.image_size, self.augmentation)
        self.training_set = MEADDataset(self.root_dir, self.output_dir, self.video_list, self.video_metas, train, 
                self.audio_metas, self.sequence_length_train, image_size=self.image_size, 
                transforms=training_augmenter,
                **self.occlusion_settings_train,
                hack_length=False,
                use_original_video=self.use_original_video,
                include_processed_audio = self.include_processed_audio,
                include_raw_audio = self.include_raw_audio,
                landmark_types=self.landmark_types,
                landmark_source=self.landmark_sources,
                segmentation_source=self.segmentation_source,
                temporal_split_start= 0 if self.temporal_split is not None else None,
                temporal_split_end=self.temporal_split[0] if self.temporal_split is not None else None,
                preload_videos=self.preload_videos,
                inflate_by_video_size=self.inflate_by_video_size,
              )
                    
        self.validation_set = MEADDataset(self.root_dir, self.output_dir, 
                self.video_list, self.video_metas, val, self.audio_metas, 
                self.sequence_length_val, image_size=self.image_size,  
                **self.occlusion_settings_val,
                hack_length=False, 
                use_original_video=self.use_original_video,
                include_processed_audio = self.include_processed_audio,
                include_raw_audio = self.include_raw_audio,
                landmark_types=self.landmark_types,
                landmark_source=self.landmark_sources,
                segmentation_source=self.segmentation_source,
                temporal_split_start=self.temporal_split[0] if self.temporal_split is not None else None,
                temporal_split_end= self.temporal_split[0] + self.temporal_split[1] if self.temporal_split is not None else None,
                preload_videos=self.preload_videos,
                inflate_by_video_size=self.inflate_by_video_size,
            )

        self.test_set = MEADDataset(self.root_dir, self.output_dir, self.video_list, self.video_metas, test, self.audio_metas, 
                self.sequence_length_test, image_size=self.image_size, 
                **self.occlusion_settings_test,
                hack_length=False, 
                use_original_video=self.use_original_video,
                include_processed_audio = self.include_processed_audio,
                include_raw_audio = self.include_raw_audio,
                landmark_types=self.landmark_types,
                landmark_source=self.landmark_sources,
                segmentation_source=self.segmentation_source,
                temporal_split_start=self.temporal_split[0] + self.temporal_split[1] if self.temporal_split is not None else None,
                temporal_split_end= sum(self.temporal_split) if self.temporal_split is not None else None,
                preload_videos=self.preload_videos,
                inflate_by_video_size=self.inflate_by_video_size,
                )

    def train_sampler(self):
        if self.training_sampler == "uniform":
            sampler = None
        else:
            raise ValueError(f"Invalid sampler value: '{self.training_sampler}'")
        return sampler

    def train_dataloader(self):
        sampler = self.train_sampler()
        dl =  DataLoader(self.training_set, shuffle=sampler is None, num_workers=self.num_workers, pin_memory=True,
                        batch_size=self.batch_size_train, drop_last=self.drop_last, sampler=sampler, 
                        collate_fn=robust_collate)
        return dl

    def val_dataloader(self):
        dl = DataLoader(self.validation_set, shuffle=False, num_workers=self.num_workers, pin_memory=True,
                          batch_size=self.batch_size_val, 
                        #   drop_last=self.drop_last
                          drop_last=False, 
                        collate_fn=robust_collate)
        if hasattr(self, "validation_set_2"): 
            dl2 =  DataLoader(self.validation_set_2, shuffle=False, num_workers=self.num_workers, pin_memory=True,
                            batch_size=self.batch_size_val, 
                            # drop_last=self.drop_last, 
                            drop_last=False, 
                            collate_fn=robust_collate)
                            
            return [dl, dl2]
        return dl 

    def test_dataloader(self):
        return torch.utils.data.DataLoader(self.test_set, shuffle=False, num_workers=self.num_workers, pin_memory=True,
                          batch_size=self.batch_size_test, drop_last=self.drop_last)

import imgaug
from gdl.datasets.VideoDatasetBase import VideoDatasetBase

class MEADDataset(VideoDatasetBase):

    def __init__(self,
            root_path,
            output_dir,
            video_list, 
            video_metas,
            video_indices,
            # audio_paths, 
            audio_metas,
            sequence_length,
            audio_noise_prob=0.0,
            stack_order_audio=4,
            audio_normalization="layer_norm",
            landmark_types=None, 
            segmentation_type = "bisenet",
            landmark_source = "original",
            segmentation_source = "aligned",
            occlusion_length=0,
            occlusion_probability_mouth = 0.0,
            occlusion_probability_left_eye = 0.0,
            occlusion_probability_right_eye = 0.0,
            occlusion_probability_face = 0.0,
            image_size=None, 
            transforms : imgaug.augmenters.Augmenter = None,
            hack_length=False,
            use_original_video=False,
            include_processed_audio = True,
            include_raw_audio = True,
            temporal_split_start=None,
            temporal_split_end=None,
            preload_videos=False,
            inflate_by_video_size=False,
            include_filename=False, # if True includes the filename of the video in the sample
    ) -> None:
        landmark_types = landmark_types or ["mediapipe", "fan"]
        super().__init__(
            root_path,
            output_dir,
            video_list, 
            video_metas,
            video_indices,
            # audio_paths, 
            audio_metas,
            sequence_length,
            audio_noise_prob=audio_noise_prob,
            stack_order_audio=stack_order_audio,
            audio_normalization=audio_normalization,
            landmark_types=landmark_types, 
            segmentation_type = segmentation_type,
            landmark_source = landmark_source,
            segmentation_source = segmentation_source,
            occlusion_length=occlusion_length,
            occlusion_probability_mouth = occlusion_probability_mouth,
            occlusion_probability_left_eye = occlusion_probability_left_eye,
            occlusion_probability_right_eye = occlusion_probability_right_eye,
            occlusion_probability_face = occlusion_probability_face,
            image_size=image_size, 
            transforms = transforms,
            hack_length=hack_length,
            use_original_video=use_original_video,
            include_processed_audio = include_processed_audio,
            include_raw_audio = include_raw_audio,
            temporal_split_start=temporal_split_start,
            temporal_split_end=temporal_split_end,
            preload_videos=preload_videos,
            inflate_by_video_size=inflate_by_video_size,
            include_filename=include_filename,
        )

    def _get_landmarks(self, index, start_frame, num_read_frames, video_fps, num_frames, sample): 
        sequence_length = self._get_sample_length(index)
        landmark_dict = {}
        landmark_validity_dict = {}
        for lti, landmark_type in enumerate(self.landmark_types):
            landmark_source = self.landmark_source[lti]
            # landmarks_dir = (Path(self.output_dir) / f"landmarks_{landmark_source}" / landmark_type /  self.video_list[self.video_indices[index]]).with_suffix("")
            landmarks_dir = (Path(self.output_dir) / f"landmarks_{landmark_source}_{landmark_type}" /  self.video_list[self.video_indices[index]]).with_suffix("")
            landmarks = []
            if landmark_source == "original":
                # landmark_list = FaceDataModuleBase.load_landmark_list(landmarks_dir / f"landmarks_{landmark_source}.pkl")  
                # landmark_list = FaceDataModuleBase.load_landmark_list(landmarks_dir / f"landmarks_aligned_video.pkl")  
                landmark_list_file = landmarks_dir / f"landmarks_aligned_video_smoothed.pkl"
                if landmark_list_file.exists():
                    landmark_list = FaceDataModuleBase.load_landmark_list(landmark_list_file)  
                    landmark_types =  FaceDataModuleBase.load_landmark_list(landmarks_dir / "landmark_types.pkl")  
                    landmarks = landmark_list[start_frame: sequence_length + start_frame] 
                    landmarks = np.stack(landmarks, axis=0)

                    landmark_valid_indices = FaceDataModuleBase.load_landmark_list(landmarks_dir / "landmarks_alignment_used_frame_indices.pkl")  
                    landmark_validity = np.zeros((len(landmark_list), 1), dtype=np.float32)
                    landmark_validity[landmark_valid_indices] = 1.0
                    landmark_validity = landmark_validity[start_frame: sequence_length + start_frame]
                else:
                    if landmark_type == "mediapipe":
                        num_landmarks = MEDIAPIPE_LANDMARK_NUMBER
                    elif landmark_type in ["fan", "kpt68"]:
                        num_landmarks = 68
                    landmarks = np.zeros((sequence_length, num_landmarks, 2), dtype=np.float32)
                    landmark_validity = np.zeros((sequence_length, 1), dtype=np.float32)
                    # landmark_validity = landmark_validity.squeeze(-1)


            elif landmark_source == "aligned":
                landmarks, landmark_confidences, landmark_types = FaceDataModuleBase.load_landmark_list_v2(landmarks_dir / f"landmarks.pkl")  

                # scale by image size 
                landmarks = landmarks * sample["video"].shape[1]

                landmarks = landmarks[start_frame: sequence_length + start_frame]
                # landmark_confidences = landmark_confidences[start_frame: sequence_length + start_frame]
                # landmark_validity = landmark_confidences #TODO: something is wrong here, the validity is not correct and has different dimensions
                landmark_validity = None 
            
            else: 
                raise ValueError(f"Invalid landmark source: '{landmark_source}'")

            # landmark_validity = np.ones(len(landmarks), dtype=np.bool)
            # for li in range(len(landmarks)): 
            #     if len(landmarks[li]) == 0: # dropped detection
            #         if landmark_type == "mediapipe":
            #             # [WARNING] mediapipe landmarks coordinates are saved in the scale [0.0-1.0] (for absolute they need to be multiplied by img size)
            #             landmarks[li] = np.zeros((478, 3))
            #         elif landmark_type in ["fan", "kpt68"]:
            #             landmarks[li] = np.zeros((68, 2))
            #         else: 
            #             raise ValueError(f"Unknown landmark type '{landmark_type}'")
            #         landmark_validity[li] = False
            #     elif len(landmarks[li]) > 1: # multiple faces detected
            #         landmarks[li] = landmarks[li][0] # just take the first one for now
            #     else: \
            #         landmarks[li] = landmarks[li][0] 

            # landmarks = np.stack(landmarks, axis=0)

            # pad landmarks with zeros if necessary to match the desired video length
            if landmarks.shape[0] < sequence_length:
                landmarks = np.concatenate([landmarks, np.zeros(
                    (sequence_length - landmarks.shape[0], *landmarks.shape[1:]), 
                    dtype=landmarks.dtype)], axis=0)
                if landmark_validity is not None:
                    landmark_validity = np.concatenate([landmark_validity, np.zeros((sequence_length - landmark_validity.shape[0], landmark_validity.shape[1]), 
                        dtype=landmark_validity.dtype)], axis=0)
                else: 
                    landmark_validity = np.zeros((sequence_length, 1), dtype=np.float32)

            landmark_dict[landmark_type] = landmarks
            if landmark_validity is not None:
                landmark_validity_dict[landmark_type] = landmark_validity

        sample["landmarks"] = landmark_dict
        sample["landmarks_validity"] = landmark_validity_dict
        return sample


    def _path_to_segmentations(self, index): 
        return (Path(self.output_dir) / f"segmentations_{self.segmentation_source}_{self.segmentation_type}" /  self.video_list[self.video_indices[index]]).with_suffix("")




def main(): 
    import time

    root_dir = Path("/ps/project/EmotionalFacialAnimation/data/mead/MEAD")
    output_dir = Path("/is/cluster/work/rdanecek/data/mead/")

    # root_dir = Path("/ps/project/EmotionalFacialAnimation/data/lrs2/mvlrs_v1")
    # output_dir = Path("/ps/scratch/rdanecek/data/lrs2")

    processed_subfolder = "processed"
    # processed_subfolder = "processed_orig"

    # seq_len = 50
    seq_len = 16
    # bs = 100
    bs = 1


    import yaml
    from munch import Munch, munchify
    # augmenter = yaml.load(open(Path(__file__).parents[2] / "gdl_apps" / "Speech4D" / "tempface_conf" / "data" / "augmentations" / "default_no_jpeg.yaml"), 
    #     Loader=yaml.FullLoader)["augmentation"]
    # augmenter = munchify(augmenter)
    augmenter = None
    
    occlusion_settings_train = None
    # occlusion_settings_train = {
    #     "occlusion_length": [5, 15],
    #     "occlusion_probability_mouth": 0.5,
    #     "occlusion_probability_left_eye": 0.33,
    #     "occlusion_probability_right_eye": 0.33,
    #     "occlusion_probability_face": 0.2,
    # }
# occlusion_settings_val:
#     occlusion_length: [5, 10]
#     occlusion_probability_mouth: 1.0
#     occlusion_probability_left_eye: 0.33
#     occlusion_probability_right_eye: 0.33
#     occlusion_probability_face: 0.2

# occlusion_settings_test:
#     occlusion_length: [5, 10]
#     occlusion_probability_mouth: 1.0
#     occlusion_probability_left_eye: 0.33
#     occlusion_probability_right_eye: 0.33
#     occlusion_probability_face: 0.2

    # Create the dataset
    dm = MEADDataModule(
        root_dir, output_dir, processed_subfolder,
        split="random",
        # split="temporal_80_10_10",
        # split="specific_video_temporal_z0ecgTX08pI_0_1_80_10_10",  # missing audio
        # split="specific_video_temporal_8oKLUz8phdg_1_0_80_10_10",
        # split="specific_video_temporal_eknCAJ0ik8c_0_0_80_10_10",
        # split="specific_video_temporal_YgJ5ZEn67tk_2_80_10_10",
        # split="specific_video_temporal_zZrDihnANpM_4_80_10_10", 
        # split = "specific_video_temporal_6jRVZQMKlxw_1_0_80_10_10",
        # split = "specific_video_temporal_6jRVZQMKlxw_1_0_80_10_10",
        # split="specific_video_temporal_T0BMVyJ1OXk_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_2T3YWtHj_Ag_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_7Eha1lreIyg_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_UHY7k99ugXc_0_2_80_10_10", # missing audio
        # split="specific_video_temporal_e4Ylz6WgBrg_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_Px5769-CPaQ_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_HhlT8RJaQEY_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_eQZ-f9Vll3c_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_IubhiJFulKk_2_0_80_10_10", # missing audio
        # split="specific_video_temporal_uYC1dIPHoRQ_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_20n3XeaEd1c_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_CWdm32em3xQ_0_80_10_10",
        # split="specific_video_temporal_Px5769-CPaQ_0_0_80_10_10",  # missing audio
        # split="specific_video_temporal_HhlT8RJaQEY_0_0_80_10_10",  # missing audio
        # split="specific_video_temporal_uYC1dIPHoRQ_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_OfVYgE_hT88_0_0_80_10_10", # missing audio
        # split="specific_video_temporal_lBwtMLK_qEE_1_0_80_10_10", # missing audio
        # split="specific_video_temporal_Gq17Orwh4c4_9_1_80_10_10", # missing audio
        # split="specific_video_temporal_-rjR4El7qzg_4_80_10_10",
        # split="specific_video_temporal_lBwtMLK_qEE_1_0_80_10_10",
        # split="specific_video_temporal_lBwtMLK_qEE_1_0_80_10_10",
        image_size=224, 
        scale=1.25, 
        processed_video_size=256,
        batch_size_train=bs,
        batch_size_val=bs,
        batch_size_test=bs,
        sequence_length_train=seq_len,
        sequence_length_val=seq_len,
        sequence_length_test=seq_len,
        num_workers=8,            
        include_processed_audio = True,
        include_raw_audio = True,
        augmentation=augmenter,
        occlusion_settings_train=occlusion_settings_train,
    )

    # Create the dataloader
    dm.prepare_data() 
    dm.setup() 

    dl = dm.train_dataloader()
    # dl = dm.val_dataloader()
    dataset = dm.training_set
    print( f"Dataset length: {len(dataset)}")
    # dataset = dm.validation_set
    indices = np.arange(len(dataset), dtype=np.int32)
    np.random.shuffle(indices)

    for i in range(len(indices)): 
        start = time.time()
        sample = dataset[indices[i]]
        end = time.time()
        print(f"Loading sample {i} took {end-start:.3f} s")
        dataset.visualize_sample(sample)

    # from tqdm import auto
    # for bi, batch in enumerate(auto.tqdm(dl)): 
    #     pass


    # iter_ = iter(dl)
    # for i in range(len(dl)): 
    #     start = time.time()
    #     batch = next(iter_)
    #     end = time.time()
    #     print(f"Loading batch {i} took {end-start:.3f} s")
        # dataset.visualize_batch(batch)

    #     break

    # dm._segment_faces_in_sequence(0)
    # idxs = np.arange(dm.num_sequences)
    # np.random.seed(0)
    # np.random.shuffle(idxs)

    # for i in range(dm.num_sequences):
    #     dm._deep_restore_sequence_sr_res(idxs[i])

    # dm.setup()



if __name__ == "__main__": 
    main()