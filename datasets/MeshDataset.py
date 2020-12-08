from torch.utils.data import Dataset
from torch.utils.data.dataloader import DataLoader
import pytorch_lightning as pl

import glob, os, sys
from pathlib import Path
import pyvista as pv
# from utils.mesh import load_mesh
# from scipy.io import wavfile
# import resampy
import numpy as np
import torch
import torchaudio
from enum import Enum
from typing import Optional, Union, List
import pickle as pkl
from collections import OrderedDict
from tqdm import tqdm


class SoundAlignment(Enum):
    START_AT = 1
    ENDS_AT = 2
    MID_AT = 3


class Emotion(Enum):
    ANGRY = 0
    DISGUSTED = 1
    EXCITED = 2
    FEARFUL = 3
    FRUSTRATED = 4
    HAPPY = 5
    NEUTRAL = 6
    SAD = 7
    SURPRISED = 8

    @staticmethod
    def fromString(s : str):
        sub = s[:3].lower()
        if sub == 'ang':
            return Emotion.ANGRY
        if sub == 'dis':
            return Emotion.DISGUSTED
        if sub == 'exc':
            return Emotion.EXCITED
        if sub == 'fea':
            return Emotion.FEARFUL
        if sub == 'fru':
            return Emotion.FRUSTRATED
        if sub == 'hap':
            return Emotion.HAPPY
        if sub == 'neu':
            return Emotion.NEUTRAL
        if sub == 'sad':
            return Emotion.SAD
        if sub == 'sur':
            return Emotion.SURPRISED
        raise ValueError("Invalid emotion string: %s" % s)


def sentenceID(s : str):
    # the filenames are named starting from 1, so make it 0 based
    return int(s[-2:])-1



class EmoSpeechDataModule(pl.LightningDataModule):

    def __init__(self,
                 # output_dir,
                 root_dir,
                 output_dir,
                 processed_subfolder = None,
                 # root_mesh_dir,
                 # root_audio_dir=None,
                 mesh_fps=60,
                 sound_target_samplerate=22020,
                 sound_alignment=SoundAlignment.MID_AT,
                 train_transforms=None,
                 val_transforms=None,
                 test_transforms=None,
                 dims=None
                 ):
        self.root_dir = root_dir
        # self.root_mesh_dir = root_mesh_dir
        # self.root_audio_dir = root_audio_dir
        self.root_mesh_dir = os.path.join(self.root_dir, "EmotionalSpeech_alignments_new", "seq_align")
        self.root_audio_dir = os.path.join(self.root_dir, "EmotionalSpeech_data", "audio")

        train_pattern = ""
        valiation_pattern = ""
        test_pattern = ""

        self.mesh_fps = mesh_fps

        self.sound_alignment = sound_alignment
        self.sound_target_samplerate = sound_target_samplerate

        assert self.sound_target_samplerate % self.mesh_fps == 0

        if processed_subfolder is None:
            import datetime
            date = datetime.datetime.now()
            processed_folder = os.path.join(output_dir, "processed_%s" % date.strftime("%Y_%b_%d_%H-%M-%S"))
        else:
            processed_folder = os.path.join(output_dir, processed_subfolder)
        self.output_dir = processed_folder


        # To be initializaed
        self.all_mesh_paths = None
        self.all_audio_paths = None
        self.subjects2sequences = None
        self.identity_name2idx = None

        self.vertex_array = None
        self.raw_audio_array = None
        self.emotion_array = None
        self.sentence_array = None
        self.identity_array = None
        self.sequence_array = None

        super().__init__(train_transforms, val_transforms, test_transforms)


    @property
    def verts_array_path(self):
        return os.path.join(self.output_dir, "verts.memmap")

    @property
    def raw_audio_array_path(self):
        return os.path.join(self.output_dir, "raw_audio.memmap")

    @property
    def emotion_array_path(self):
        return os.path.join(self.output_dir, "emotion.pkl")

    @property
    def identity_array_path(self):
        return os.path.join(self.output_dir, "identity.pkl")

    @property
    def sentence_array_path(self):
        return os.path.join(self.output_dir, "sentence.pkl")

    @property
    def sequence_array_path(self):
        return os.path.join(self.output_dir, "sequence.pkl")

    @property
    def sequence_length_array_path(self):
        return os.path.join(self.output_dir, "sequence_length.pkl")

    @property
    def templates_path(self):
        return os.path.join(self.output_dir, "templates.pkl")

    @property
    def metadata_path(self):
        return os.path.join(self.output_dir, "metadata.pkl")

    @property
    def personalized_template_paths(self):
        return [ os.path.join(self.root_dir, "EmotionalSpeech_alignments_new", "personalization", "personalized_template", subject + ".ply")
                 for subject in self.subjects2sequences.keys() ]

    @property
    def num_audio_samples_per_scan(self):
        return int(self.sound_target_samplerate / self.mesh_fps)

    @property
    def num_samples(self):
        return len(self.all_mesh_paths)

    @property
    def num_verts(self):
        return self.subjects_templates[0].number_of_points

    @property
    def version(self):
        return 1

    def _load_templates(self):
        self.subjects_templates = [pv.read(template_path) for template_path in self.personalized_template_paths]

    def prepare_data(self, *args, **kwargs):
        outdir = Path(self.output_dir)

        # is dataset already processed?
        if outdir.is_dir():
            print("The dataset is already processed")
            self._load_templates()
            self._loadArrays()
            self._loadMeta()
            return

        self._gather_data()

        self._load_templates()
        # create data arrays
        self.vertex_array = np.memmap(self.verts_array_path, dtype=np.float32, mode='w+',
                                      shape=(self.num_samples,3*self.num_verts))
        self.raw_audio_array = np.memmap(self.raw_audio_array_path, dtype=np.float32, mode='w+', shape=(self.num_samples, self.num_audio_samples_per_scan))

        self.emotion_array = np.zeros(dtype=np.int32, shape=(self.num_samples, 1))
        self.sentence_array = np.zeros(dtype=np.int32, shape=(self.num_samples, 1))
        self.identity_array = np.zeros(dtype=np.int32, shape=(self.num_samples, 1))
        self.sequence_array = np.zeros(dtype=np.int32, shape=(self.num_samples, 1))
        self.sequence_length_array = np.zeros(dtype=np.int32, shape=(self.num_samples, 1))

        # populate data arrays
        self._process_data()

        with open(self.emotion_array_path, "wb") as f:
            pkl.dump(self.emotion_array, f)
        with open(self.sentence_array_path, "wb") as f:
            pkl.dump(self.sentence_array, f)
        with open(self.identity_array_path, "wb") as f:
            pkl.dump(self.identity_array, f)
        with open(self.sequence_array_path, "wb") as f:
            pkl.dump(self.sequence_array, f)
        with open(self.sequence_length_array_path, "wb") as f:
            pkl.dump(self.sequence_length_array, f)
        with open(self.templates_path, "wb") as f:
            pkl.dump(self.subjects_templates, f)

        self._saveMeta()

        # close data arrays
        self._cleanupMemmaps()
        self._loadArrays()

    def _saveMeta(self):
        with open(self.metadata_path, "wb") as f:
            pkl.dump(self.version, f)
            pkl.dump(self.all_mesh_paths, f)
            pkl.dump(self.all_audio_paths, f)
            pkl.dump(self.subjects2sequences, f)
            pkl.dump(self.identity_name2idx, f)
            pkl.dump(self.sound_alignment, f)

    def _loadMeta(self):
        with open(self.metadata_path, "wb") as f:
            version = pkl.load(f)
            self.all_mesh_paths = pkl.load(f)
            self.all_audio_paths = pkl.load(f)
            self.subjects2sequences = pkl.load(f)
            self.identity_name2idx = pkl.load(f)
            self.sound_alignment = pkl.load(f)

    def _gather_data(self):
        print("Processing dataset")
        Path(self.output_dir).mkdir(parents=True)
        root_mesh_path = Path(self.root_mesh_dir)
        # root_audio_path = Path(self.root_audio_dir)

        pattern = root_mesh_path / "*"
        subjects = sorted([os.path.basename(dir) for dir in glob.glob(pattern.as_posix()) if os.path.isdir(dir)])

        self.identity_name2idx = OrderedDict(zip(subjects, range(len(subjects))))
        self.subjects2sequences = OrderedDict()
        self.all_mesh_paths = []

        print("Discovering data")
        for subject in subjects:
            print("Found subject: '%s'" % subject)
            subject_path = root_mesh_path / subject
            sequences = sorted([dir.name for dir in subject_path.iterdir() if dir.is_dir()])
            # sequences = sorted([os.path.basename(dir) for dir in glob.glob(subject_path.as_posix()) if os.path.isdir(dir)])
            seq2paths = OrderedDict()
            for sequence in tqdm(sequences):
                mesh_paths = sorted(list((subject_path / sequence).glob("*.ply")))
                relative_mesh_paths = [path.relative_to(self.root_mesh_dir) for path in mesh_paths]
                seq2paths[sequence] = relative_mesh_paths
                self.all_mesh_paths += relative_mesh_paths

                # if self.root_audio_dir is not None:
                #     audio_file = root_audio_path / subject / "scanner" / (sequence + ".wav")
                #     # sample_rate, audio_data = wavfile.read(audio_file)
                #     audio_data, sample_rate = torchaudio.load(audio_file)
                #     if sample_rate != self.sound_target_samplerate:
                #         # audio_data_resampled = resampy.resample(audio_data.astype(np.float64), sample_rate, self.sound_target_samplerate)
                #         audio_data_resampled = torchaudio.transforms.Resample(sample_rate, self.sound_target_samplerate)(audio_data[0, :].view(1, -1))
                #         num_sound_samples = audio_data_resampled.shape[1]
                #         samples_per_scan = self.num_audio_samples_per_scan
                #
                #         num_meshes_in_sequence = len(mesh_paths)
                #         assert ((num_meshes_in_sequence)*samples_per_scan >= num_sound_samples and
                #                 (num_meshes_in_sequence-1)*samples_per_scan <= num_sound_samples)
                #
                #         audio_data_aligned = torch.zeros((1, samples_per_scan*num_meshes_in_sequence), dtype=audio_data_resampled.dtype)
                #         if self.sound_alignment == SoundAlignment.START_AT:
                #             # padd zeros to the end
                #             start_at = 0
                #         elif self.sound_alignment == SoundAlignment.ENDS_AT:
                #             # pad zeros to the beginning
                #             start_at = self.mesh_fps
                #         elif self.sound_alignment == SoundAlignment.MID_AT:
                #             start_at = int(self.mesh_fps / 2)
                #             assert self.mesh_fps % 2 == 0
                #         else:
                #             raise ValueError("Invalid sound alignment '%s' " % str(self.sound_alignment))
                #         audio_data_aligned[:, start_at:start_at+audio_data_resampled.shape[1]] = audio_data_resampled[:,...]

            self.subjects2sequences[subject] = seq2paths

    def _process_data(self):
        mesh_idx = 0
        sequence_idx = 0
        sequence_lengths = []

        self.all_audio_paths = []
        print("Processing discovered data")
        with tqdm(total=self.num_samples) as pbar:

            for subject_name, sequences in self.subjects2sequences.items():
                for seq_name, meshes in sequences.items():
                    print("Starting processing sequence: '%s' of subject '%s'" % (seq_name, subject_name))

                    sentence_number = sentenceID(seq_name)

                    num_meshes_in_sequence = len(meshes)

                    audio_subpath = Path(subject_name) / "scanner" / (seq_name + ".wav")
                    audio_file = Path(self.root_audio_dir) / audio_subpath
                    self.all_audio_paths += [audio_subpath]
                    # sample_rate, audio_data = wavfile.read(audio_file)
                    audio_data, sample_rate = torchaudio.load(audio_file)

                    if sample_rate != self.sound_target_samplerate:
                        # audio_data_resampled = resampy.resample(audio_data.astype(np.float64), sample_rate, self.sound_target_samplerate)
                        audio_data_resampled = torchaudio.transforms.Resample(sample_rate, self.sound_target_samplerate)(
                            audio_data[0, :].view(1, -1))
                    else:
                        audio_data_resampled = audio_data

                    num_sound_samples = audio_data_resampled.shape[1]
                    samples_per_scan = self.num_audio_samples_per_scan

                    # if not (num_meshes_in_sequence * self.num_audio_samples_per_scan >= num_sound_samples and
                    #         (num_meshes_in_sequence - 1) * self.num_audio_samples_per_scan <= num_sound_samples):
                    #     print("Num sound samples: %d" % num_sound_samples)
                    #     print("Expected range: %d - %d" %
                    #           (num_meshes_in_sequence * self.num_audio_samples_per_scan, (num_meshes_in_sequence-1) * self.num_audio_samples_per_scan)
                    #           )
                    # assert ((num_meshes_in_sequence) * self.num_audio_samples_per_scan >= num_sound_samples and
                    #         (num_meshes_in_sequence - 1) * self.num_audio_samples_per_scan <= num_sound_samples)

                    aligned_array_size = samples_per_scan * num_meshes_in_sequence

                    audio_data_aligned = torch.zeros((1, aligned_array_size),
                                                     dtype=audio_data_resampled.dtype)

                    if self.sound_alignment == SoundAlignment.START_AT:
                        # padd zeros to the end
                        start_at = 0
                    elif self.sound_alignment == SoundAlignment.ENDS_AT:
                        # pad zeros to the beginning
                        start_at = self.mesh_fps
                    elif self.sound_alignment == SoundAlignment.MID_AT:
                        start_at = int(self.mesh_fps / 2)
                        assert self.mesh_fps % 2 == 0
                    else:
                        raise ValueError("Invalid sound alignment '%s' " % str(self.sound_alignment))
                    length = min(audio_data_resampled.shape[1], audio_data_aligned.shape[1] - start_at)
                    audio_data_aligned[:, start_at:(start_at + length)] = audio_data_resampled[:, :length]

                    for i, mesh_name in enumerate(meshes):
                        mesh = pv.read( Path(self.root_mesh_dir) / mesh_name)
                        self.vertex_array[mesh_idx, :] = np.reshape(mesh.points, newshape=(1, -1))
                        self.raw_audio_array[mesh_idx, :] = audio_data_aligned[0, i * self.num_audio_samples_per_scan:(i + 1) * self.num_audio_samples_per_scan].numpy()

                        self.emotion_array[mesh_idx, 0] = Emotion.fromString(seq_name).value
                        self.identity_array[mesh_idx, 0] = self.identity_name2idx[subject_name]
                        self.sentence_array[mesh_idx, 0] = sentence_number
                        self.sequence_array[mesh_idx, 0] = sequence_idx

                        mesh_idx += 1
                        pbar.update()

                    print("Done processing sequence: '%s' of subject '%s'" % (seq_name, subject_name))
                    sequence_lengths += [len(meshes)]
                    sequence_idx += 1

        self.sequence_length_array = np.array(sequence_lengths, dtype=np.int32)


    def _cleanupMemmaps(self):
        if self.vertex_array is not None:
            del self.vertex_array
            self.vertex_array = None
        if self.raw_audio_array is not None:
            del self.raw_audio_array
            self.raw_audio_array = None

    def __del__(self):
        self._cleanupMemmaps()

    def _loadArrays(self):
        # load data arrays in read mode

        self.vertex_array = np.memmap(self.verts_array_path, dtype='float32', mode='r', shape=(self.num_samples, 4))
        self.raw_audio_array = np.memmap(self.raw_audio_array_path, dtype='float32', mode='r', shape=(self.num_samples, 4))

        with open(self.emotion_array_path, "rb") as f:
            self.emotion_array = pkl.load(f)

        with open(self.sentence_array_path, "rb") as f:
            self.sentence_array = pkl.load(f)

        with open(self.sentence_array_path, "rb") as f:
            self.sequence_array = pkl.load(f)

        with open(self.identity_array_path, "rb") as f:
            self.identity_array = pkl.load(f)

        with open(self.sequence_length_array_path, "rb") as f:
            self.sequence_length_array = pkl.load(f)

        with open(self.templates_path, "rb") as f:
            self.subjects_templates = pkl.load(f)


    def setup(self, stage: Optional[str] = None):
        pass

    def train_dataloader(self, *args, **kwargs) -> DataLoader:
        pass

    def val_dataloader(self, *args, **kwargs) -> Union[DataLoader, List[DataLoader]]:
        pass

    def test_dataloader(self, *args, **kwargs) -> Union[DataLoader, List[DataLoader]]:
        pass


class EmoSpeechDataset(Dataset):

    def __init__(self, root_mesh_dir, root_audio_dir=None, mesh_fps=60, sound_alignment=SoundAlignment.ENDS_AT):

        self.root_mesh_dir = root_mesh_dir
        self.root_audio_dir = root_audio_dir
        self.mesh_fps = mesh_fps
        self.sound_alignment = sound_alignment
        self.sound_target_samplerate = 22020


    def __getitem__(self, index):
        mesh_fname = self.mesh_paths[index]
        # vertices, faces = load_mesh(mesh_fname)
        # load_mesh(filename=mesh_fname)
        sample = {
            "mesh_path": mesh_fname,
            # "vertices" : vertices,
            # "faces": faces,
            "emotion": None
        }

        return sample


def main():
    root_dir = "/home/rdanecek/Workspace/mount/project/emotionalspeech/EmotionalSpeech/"
    processed_dir = "/home/rdanecek/Workspace/mount/scratch/rdanecek/EmotionalSpeech/"
    dataset = EmoSpeechDataModule(root_dir, processed_dir)
    dataset.prepare_data()
    # sample = dataset[0]
    print("Peace out")





if __name__ == "__main__":
    main()
