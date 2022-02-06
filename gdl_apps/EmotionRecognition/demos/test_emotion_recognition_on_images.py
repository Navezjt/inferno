from gdl_apps.EmotionRecognition.utils.io import load_model, test
from gdl.datasets.ImageTestDataset import TestData
import gdl
import numpy as np
import os
import torch
from skimage.io import imsave
from pathlib import Path
from tqdm import auto
import argparse
import matplotlib.pyplot as plt
from torch.functional import F
from gdl.datasets.AffectNetDataModule import AffectNetExpressions
from gdl.utils.other import get_path_to_assets
import pickle as pkl


def save_annotation(batch, predictions, output_folder):
    softmax = F.softmax(predictions["expr_classification"])
    top_expr =  torch.argmax(softmax, dim=1)
    for i in range(len(batch["image"])):
        valence = predictions["valence"][i].item()
        arousal = predictions["arousal"][i].item()
        out_fname = Path(output_folder) / f"{batch['image_name'][i]}.pkl"

        with open(out_fname, 'wb') as f:
            pkl.dump({"valence": valence, 
                      "arousal": arousal, 
                      "top_expression": top_expr[i],
                      "expression": softmax[i].detach().cpu().numpy()
                      }, f)


def save_images(batch, predictions, output_folder):
    # Save the images

    softmax = F.softmax(predictions["expr_classification"])
    top_expr =  torch.argmax(softmax, dim=1)
    for i in range(len(batch["image"])):
        img = batch["image"][i].cpu().detach().numpy()
        img = img.transpose(1, 2, 0)
        img = img * 255
        img = img.astype(np.uint8)

        plt.figure()
        # plot the image with matplotlib 
        plt.imshow(img)
        # write valence and arousal to the image
        expr = AffectNetExpressions(int(top_expr[i].item()))
        text = "Predicted emotion:\n"
        text += f'Arousal: {predictions["arousal"][i].item():.2f} \nValence: {predictions["valence"][i].item():.2f}'
        text += f"\nExpression: {expr.name}, {softmax[i][expr.value].item()*100:.2f}%"
        plt.title(text)
        out_fname = Path(output_folder) / f"{batch['image_name'][i]}.png"
        # save the image to the output folder
        
        # axis off 
        plt.axis('off')
        
        plt.savefig(out_fname)
        plt.close()


def main():
    parser = argparse.ArgumentParser()
    # add the input folder arg 
    parser.add_argument('--input_folder', type=str, default="/ps/scratch/rdanecek/EMOCA/InputImages")
    parser.add_argument('--output_folder', type=str, default="/ps/scratch/rdanecek/EMOCA/TestImages", help="Output folder to save the results to.")
    parser.add_argument('--model_type', type=str, default="3dmm", choices=["image", "3dmm"], help="Type of the model. Image-based vs face reconsruction-based")
    parser.add_argument('--model_name', type=str, default='EMOCA-emorec', help='Name of the model to use.')
    # parser.add_argument('--model_name', type=str, default='ResNet50', help='Name of the model to use.')
    parser.add_argument('--path_to_models', type=str, default=get_path_to_assets() /"EmotionRecognition")
    parser.add_argument('--save_images', type=bool, default=True, help="If true, output images will be saved")
    parser.add_argument('--save_annotation', type=bool, default=True, help="If true, output the valence, arousal and expression values")
    
    args = parser.parse_args()

    path_to_models = args.path_to_models 
    if args.model_type == "image": 
        path_to_models = path_to_models / "image_based_networks"
    elif args.model_type == "3dmm": 
        path_to_models = path_to_models / "facerec_based_models"
    input_folder = args.input_folder
    output_folder = args.output_folder
    model_name = args.model_name

    mode = 'detail'
    # mode = 'coarse'

    # 1) Load the model
    model = load_model(Path(path_to_models) / model_name)
    model.cuda()
    model.eval()

    # 2) Create a dataset
    dataset = TestData(input_folder, face_detector="fan", max_detection=20)

    ## 3) Run the model on the data
    for i in auto.tqdm( range(len(dataset))):
        batch = dataset[i]
        batch["image"] = batch["image"].cuda()
        output = model(batch)
        
        if args.save_images:
            save_images(batch, output, output_folder)
        if args.save_annotation:
            save_annotation(batch, output, output_folder)

    print("Done")


if __name__ == '__main__':
    main()
