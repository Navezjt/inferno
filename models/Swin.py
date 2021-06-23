import os, sys
from pathlib import Path
# swin_path = (Path(__file__).parents[2] / "Swin-Transformer").absolute()
swin_path = (Path(__file__).parents[2] / "SwinTransformer").absolute()
if not swin_path.is_dir():
    raise ImportError(f"Swin repository not found in : '{swin_path}'")
if str(swin_path.parent) not in sys.path:
    sys.path += [str(swin_path.parent)]

from SwinTransformer.models.build import build_model
from omegaconf import open_dict
import torch


def create_swin_backbone(swin_cfg, num_classes, img_size, load_pretrained_swin=False, pretrained_model=None):

    with open_dict(swin_cfg):
        swin_cfg.MODEL.NUM_CLASSES = num_classes
        swin_cfg.MODEL.SWIN.PATCH_SIZE = 4
        swin_cfg.MODEL.SWIN.IN_CHANS = 3
        swin_cfg.MODEL.SWIN.MLP_RATIO = 4.
        swin_cfg.MODEL.SWIN.QKV_BIAS = True
        swin_cfg.MODEL.SWIN.QK_SCALE = None
        swin_cfg.MODEL.SWIN.APE = False
        swin_cfg.MODEL.SWIN.PATCH_NORM = True

        # Dropout rate
        if 'DROP_RATE' not in swin_cfg.MODEL.keys():
            swin_cfg.MODEL.DROP_RATE = 0.0
        # Drop path rate
        if 'DROP_PATH_RATE' not in swin_cfg.MODEL.keys():
            swin_cfg.MODEL.DROP_PATH_RATE = 0.1
        # Label Smoothing

        if 'DROP_PATH_RATE' not in swin_cfg.MODEL.keys():
            swin_cfg.MODEL.LABEL_SMOOTHING = 0.1

        swin_cfg.DATA = {}
        swin_cfg.DATA.IMG_SIZE = img_size

        swin_cfg.TRAIN = {}
        swin_cfg.TRAIN.USE_CHECKPOINT = False

        # # Swin Transformer parameters
        # _C.MODEL.SWIN = CN()
        # _C.MODEL.SWIN.PATCH_SIZE = 4
        # _C.MODEL.SWIN.IN_CHANS = 3
        # _C.MODEL.SWIN.EMBED_DIM = 96
        # _C.MODEL.SWIN.DEPTHS = [2, 2, 6, 2]
        # _C.MODEL.SWIN.NUM_HEADS = [3, 6, 12, 24]
        # _C.MODEL.SWIN.WINDOW_SIZE = 7
        # _C.MODEL.SWIN.MLP_RATIO = 4.
        # _C.MODEL.SWIN.QKV_BIAS = True
        # _C.MODEL.SWIN.QK_SCALE = None
        # _C.MODEL.SWIN.APE = False
        # _C.MODEL.SWIN.PATCH_NORM = True

    swin = build_model(swin_cfg)

    if load_pretrained_swin:
        # load the pretrained model from the official repo
        path_to_model = Path(__file__).parents[2] / "SwinTransformer" / "pretrained_models" / (
                    pretrained_model + ".pth")
        state_dict = torch.load(path_to_model)
        # delete the head of the model from the state_dict - we have a different number of outputs
        del state_dict['model']['head.weight']
        del state_dict['model']['head.bias']
        swin.load_state_dict(state_dict['model'], strict=False)
        print(f"Loading pretrained model from '{path_to_model}'")

    return swin