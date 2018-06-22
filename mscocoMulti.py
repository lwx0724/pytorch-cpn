import os
import numpy as np
import json
import random
import math
import cv2
import skimage
import skimage.transform

import torch
import torch.utils.data as data

from utils.osutils import *
from utils.imutils import *
from utils.transforms import *

class MscocoMulti(data.Dataset):
    def __init__(self, jsonfile, img_folder, symmetry, pixel_means, inp_res=(256, 192), out_res=(64, 48), train=True,
                 bbox_extend_factor = (0.1, 0.15), scale_factor=(0.7, 1.35), rot_factor=45, num_class=17):
        self.img_folder = img_folder
        self.is_train = train
        self.inp_res = inp_res
        self.out_res = out_res
        self.scale_factor = scale_factor
        self.rot_factor = rot_factor
        self.bbox_extend_factor = bbox_extend_factor
        self.symmetry = symmetry
        self.pixel_means = pixel_means
        self.num_class = num_class

        with open(jsonfile) as anno_file:   
            self.anno = json.load(anno_file)

    def augmentationCropImage(self, img, bbox, joints):  
        height, width = self.inp_res[0], self.inp_res[1]
        bbox = np.array(bbox).reshape(4, ).astype(np.float32)
        add = max(img.shape[0], img.shape[1])
        mean_value = self.pixel_means
        bimg = cv2.copyMakeBorder(img, add, add, add, add, borderType=cv2.BORDER_CONSTANT, value=mean_value.tolist())
        objcenter = np.array([(bbox[0] + bbox[2]) / 2., (bbox[1] + bbox[3]) / 2.])      
        bbox += add
        objcenter += add
        joints[:, :2] += add
        inds = np.where(joints[:, -1] == 0)
        joints[inds, :2] = -1000000 # avoid influencing by data processing
        crop_width = (bbox[2] - bbox[0]) * (1 + self.bbox_extend_factor[0] * 2)
        crop_height = (bbox[3] - bbox[1]) * (1 + self.bbox_extend_factor[1] * 2)
        if self.is_train:
            crop_width = crop_width * (1 + 0.25)
            crop_height = crop_height * (1 + 0.25)  
        if crop_height / height > crop_width / width:
            crop_size = crop_height
            min_shape = height
        else:
            crop_size = crop_width
            min_shape = width  

        crop_size = min(crop_size, objcenter[0] / width * min_shape * 2. - 1.)
        crop_size = min(crop_size, (bimg.shape[1] - objcenter[0]) / width * min_shape * 2. - 1)
        crop_size = min(crop_size, objcenter[1] / height * min_shape * 2. - 1.)
        crop_size = min(crop_size, (bimg.shape[0] - objcenter[1]) / height * min_shape * 2. - 1)

        min_x = int(objcenter[0] - crop_size / 2. / min_shape * width)
        max_x = int(objcenter[0] + crop_size / 2. / min_shape * width)
        min_y = int(objcenter[1] - crop_size / 2. / min_shape * height)
        max_y = int(objcenter[1] + crop_size / 2. / min_shape * height)                               

        x_ratio = float(width) / (max_x - min_x)
        y_ratio = float(height) / (max_y - min_y)

        joints[:, 0] = joints[:, 0] - min_x
        joints[:, 1] = joints[:, 1] - min_y

        joints[:, 0] *= x_ratio
        joints[:, 1] *= y_ratio
        label = joints[:, :2].copy()
        valid = joints[:, 2].copy()

        img = cv2.resize(bimg[min_y:max_y, min_x:max_x, :], (width, height))  
        details = np.asarray([min_x - add, min_y - add, max_x - add, max_y - add]) 
        return img, joints, details



    def data_augmentation(self, img, label, operation):
        height, width = img.shape[0], img.shape[1]
        center = (width / 2., height / 2.)
        n = label.shape[0]
        affrat = random.uniform(self.scale_factor[0], self.scale_factor[1])
        
        halfl_w = min(width - center[0], (width - center[0]) / 1.25 * affrat)
        halfl_h = min(height - center[1], (height - center[1]) / 1.25 * affrat)
        img = skimage.transform.resize(img[int(center[1] - halfl_h): int(center[1] + halfl_h + 1),
                             int(center[0] - halfl_w): int(center[0] + halfl_w + 1)], (height, width))
        for i in range(n):
            label[i][0] = (label[i][0] - center[0]) / halfl_w * (width - center[0]) + center[0]
            label[i][1] = (label[i][1] - center[1]) / halfl_h * (height - center[1]) + center[1]
            label[i][2] *= (
            (label[i][0] >= 0) & (label[i][0] < width) & (label[i][1] >= 0) & (label[i][1] < height))

        # flip augmentation
        if operation == 1:
            img = cv2.flip(img, 1)
            cod = []
            allc = []
            for i in range(n):
                x, y = label[i][0], label[i][1]
                if x >= 0:
                    x = width - 1 - x
                cod.append((x, y, label[i][2]))
            # **** the joint index depends on the dataset ****    
            for (q, w) in self.symmetry:
                cod[q], cod[w] = cod[w], cod[q]
            for i in range(n):
                allc.append(cod[i][0])
                allc.append(cod[i][1])
                allc.append(cod[i][2])
            label = np.array(allc).reshape(n, 3)

        # rotated augmentation
        if operation > 1:      
            angle = random.uniform(0, self.rot_factor)
            if random.randint(0, 1):
                angle *= -1
            rotMat = cv2.getRotationMatrix2D(center, angle, 1.0)
            img = cv2.warpAffine(img, rotMat, (width, height))
            
            allc = []
            for i in range(n):
                x, y = label[i][0], label[i][1]
                coor = np.array([x, y])
                if x >= 0 and y >= 0:
                    R = rotMat[:, : 2]
                    W = np.array([rotMat[0][2], rotMat[1][2]])
                    coor = np.dot(R, coor) + W
                allc.append(coor[0])
                allc.append(coor[1])
                in_bound = 1 if ((coor[0] >= 0) & (coor[0] < width) & (coor[1] >= 0) & (coor[1] < height)) else 0
                allc.append(in_bound)
            label = np.array(allc).reshape(n, 3)
        return img, label

  
    def __getitem__(self, index):
        a = self.anno[index]
        image_name = a['imgInfo']['img_paths']
        img_path = os.path.join(self.img_folder, image_name)
        points = np.array(a['unit']['keypoints']).reshape(self.num_class, 3).astype(np.float32)
        gt_bbox = a['unit']['GT_bbox']

        image = scipy.misc.imread(img_path, mode='RGB')
        image, points, details = self.augmentationCropImage(image, gt_bbox, points)

        if self.is_train:
            image, points = self.data_augmentation(image, points, a['operation'])  
            img = im_to_torch(image)  # CxHxW
            
            # Color dithering
            img[0, :, :].mul_(random.uniform(0.8, 1.2)).clamp_(0, 1)
            img[1, :, :].mul_(random.uniform(0.8, 1.2)).clamp_(0, 1)
            img[2, :, :].mul_(random.uniform(0.8, 1.2)).clamp_(0, 1)

            points[:, :2] /= 4 # output size is 1/4 input size
            pts = torch.Tensor(points)
        
        img = color_normalize(img, self.pixel_means)

        if self.is_train:
            target15 = np.zeros((self.num_class, self.out_res[0], self.out_res[1]))
            target11 = np.zeros((self.num_class, self.out_res[0], self.out_res[1]))
            target9 = np.zeros((self.num_class, self.out_res[0], self.out_res[1]))
            target7 = np.zeros((self.num_class, self.out_res[0], self.out_res[1]))
            for i in range(self.num_class):
                if pts[i, 2] > 0: # COCO visible: 0-no label, 1-label + invisible, 2-label + visible
                    target15[i] = generate_heatmap(target15[i], pts[i], (15, 15))
                    target11[i] = generate_heatmap(target11[i], pts[i], (11, 11))
                    target9[i] = generate_heatmap(target9[i], pts[i], (9, 9))
                    target7[i] = generate_heatmap(target7[i], pts[i], (7, 7))
                    
            targets = [torch.Tensor(target15), torch.Tensor(target11), torch.Tensor(target9), torch.Tensor(target7)]
            valid = pts[:, 2]

        meta = {'index' : index, 'imgID' : a['imgInfo']['imgID'], 
        'GT_bbox' : np.array([gt_bbox[0], gt_bbox[1], gt_bbox[2], gt_bbox[3]]), 
        'img_path' : img_path, 'augmentation_details' : details}

        return img, targets, valid, meta

    def __len__(self):
        return len(self.anno)


