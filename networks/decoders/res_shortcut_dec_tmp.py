# ------------------------------------------------------------------------
# Modified from MGMatting (https://github.com/yucornetto/MGMatting)
# ------------------------------------------------------------------------
import logging
import torch.nn as nn
from   networks.ops import SpectralNorm
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional
from torch import Tensor

def conv5x5(in_planes, out_planes, stride=1, groups=1, dilation=1):
    """5x5 convolution with padding"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=5, stride=stride,
                     padding=2, groups=groups, bias=False, dilation=dilation)

def conv3x3(in_planes, out_planes, stride=1, groups=1, dilation=1):
    """3x3 convolution with padding"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride,
                     padding=dilation, groups=groups, bias=False, dilation=dilation)

def conv1x1(in_planes, out_planes, stride=1):
    """1x1 convolution"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=stride, bias=False)

class ConvRNN(nn.Module):
    def __init__(self,
                channels: int,
                kernel_size: int = 3,
                padding: int = 1):
        super().__init__()
        self.channels = channels
        self.ih = nn.Conv2d(channels, channels, kernel_size=kernel_size, padding=padding)
        self.hh = nn.Conv2d(channels, channels, kernel_size=kernel_size, padding=padding)
        self.ac = nn.Tanh()

    def forward(self, x):
        B, T, C, H, W = x.shape
        tmp_features = []
        for i in range(T):
            tmp_f = x[:,i]
            if i == 0:
                h = self.ac(self.ih(tmp_f))
                tmp_features.append(h.unsqueeze(1))
            else:
                h = self.ac(self.ih(tmp_f) + self.hh(h))
                tmp_features.append(h.unsqueeze(1))
        tmp_x = torch.cat(tmp_features, 1)
        return tmp_x

class BasicTmpBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, upsample=None, norm_layer=None, temporal=False, large_kernel=False, dec_T=5, dec_B=2):
        super(BasicTmpBlock, self).__init__()
        if norm_layer is None:
            norm_layer = nn.BatchNorm2d
        self.stride = stride
        conv = conv5x5 if large_kernel else conv3x3
        # Both self.conv1 and self.downsample layers downsample the input when stride != 1
        if self.stride > 1:
            self.conv1 = SpectralNorm(nn.ConvTranspose2d(inplanes, inplanes, kernel_size=4, stride=2, padding=1, bias=False))
        else:
            self.conv1 = SpectralNorm(conv(inplanes, inplanes))
        self.bn1 = norm_layer(inplanes)
        self.activation = nn.LeakyReLU(0.2, inplace=True)
        self.conv2 = SpectralNorm(conv(inplanes, planes))
        self.bn2 = norm_layer(planes)
        self.upsample = upsample
        self.temporal = temporal
        if self.upsample is not None and self.temporal is True: 
            self.tmp_network = ConvRNN(inplanes // 4)
        self.T = dec_T
        self.B = dec_B
        self.channel = inplanes // 2

    def forward_temporal(self, x):
        x = x.unflatten(0, (self.B, self.T))
        a, b = x.split(self.channel // 2, dim=2)
        b_tmp = self.tmp_network(b)
        x = torch.cat([a, b_tmp], dim=2)
        x = x.flatten(0, 1)
        return x

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.activation(out)

        out = self.conv2(out)
        out = self.bn2(out)
        if self.upsample is not None:
            identity = self.upsample(x)
            if self.temporal is True:
                identity = self.forward_temporal(identity)

        out += identity
        out = self.activation(out)

        return out

class ResShortCut_D_Dec_tmp(nn.Module):

    def __init__(self, block, layers, dec_T=5, dec_B=2, norm_layer=None, large_kernel=False, late_downsample=False):
        super(ResShortCut_D_Dec_tmp, self).__init__()
        self.logger = logging.getLogger("Logger")
        if norm_layer is None:
            norm_layer = nn.BatchNorm2d
        self._norm_layer = norm_layer
        self.large_kernel = large_kernel
        self.kernel_size = 5 if self.large_kernel else 3

        self.inplanes = 512 if layers[0] > 0 else 256
        self.late_downsample = late_downsample
        self.midplanes = 64 if late_downsample else 32

        self.conv1 = SpectralNorm(nn.ConvTranspose2d(self.midplanes, 32, kernel_size=4, stride=2, padding=1, bias=False))
        self.bn1 = norm_layer(32)
        self.leaky_relu = nn.LeakyReLU(0.2, inplace=True)

        self.upsample = nn.UpsamplingNearest2d(scale_factor=2)
        self.tanh = nn.Tanh()
        self.layer1 = self._make_layer(block, 256, layers[0], stride=2, dec_T=dec_T, dec_B=dec_B, temporal=False)
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2, dec_T=dec_T, dec_B=dec_B, temporal=False)
        self.layer3 = self._make_layer(block, 64, layers[2], stride=2, dec_T=dec_T, dec_B=dec_B, temporal=False)
        self.layer4 = self._make_layer(block, self.midplanes, layers[3], stride=2, dec_T=dec_T, dec_B=dec_B, temporal=True)

        self.refine_OS1 = nn.Sequential(
            nn.Conv2d(32, 32, kernel_size=self.kernel_size, stride=1, padding=self.kernel_size//2, bias=False),
            norm_layer(32),
            self.leaky_relu,
            nn.Conv2d(32, 2, kernel_size=self.kernel_size, stride=1, padding=self.kernel_size//2),)
        
        self.refine_OS4 = nn.Sequential(
            nn.Conv2d(64, 32, kernel_size=self.kernel_size, stride=1, padding=self.kernel_size//2, bias=False),
            norm_layer(32),
            self.leaky_relu,
            nn.Conv2d(32, 2, kernel_size=self.kernel_size, stride=1, padding=self.kernel_size//2),)

        self.refine_OS8 = nn.Sequential(
            nn.Conv2d(128, 32, kernel_size=self.kernel_size, stride=1, padding=self.kernel_size//2, bias=False),
            norm_layer(32),
            self.leaky_relu,
            nn.Conv2d(32, 2, kernel_size=self.kernel_size, stride=1, padding=self.kernel_size//2),)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                if hasattr(m, "weight_bar"):
                    nn.init.xavier_uniform_(m.weight_bar)
                else:
                    nn.init.xavier_uniform_(m.weight)
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        # Zero-initialize the last BN in each residual branch,
        # so that the residual branch starts with zeros, and each residual block behaves like an identity.
        # This improves the model by 0.2~0.3% according to https://arxiv.org/abs/1706.02677
        for m in self.modules():
            if isinstance(m, BasicTmpBlock):
                nn.init.constant_(m.bn2.weight, 0)

        self.logger.debug(self)

    def _make_layer(self, block, planes, blocks, stride=1, dec_T=5, dec_B=2, temporal=False):
        if blocks == 0:
            return nn.Sequential(nn.Identity())
        norm_layer = self._norm_layer
        upsample = None
        if stride != 1:
            upsample = nn.Sequential(
                nn.UpsamplingNearest2d(scale_factor=2),
                SpectralNorm(conv1x1(self.inplanes, planes * block.expansion)),
                norm_layer(planes * block.expansion),
            )
        elif self.inplanes != planes * block.expansion:
            upsample = nn.Sequential(
                SpectralNorm(conv1x1(self.inplanes, planes * block.expansion)),
                norm_layer(planes * block.expansion),
            )

        layers = [block(self.inplanes, planes, stride, upsample, norm_layer, temporal, self.large_kernel, dec_T, dec_B)]
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes, norm_layer=norm_layer, large_kernel=self.large_kernel, dec_T=dec_T, dec_B=dec_B))

        return nn.Sequential(*layers)

    def forward(self, x, mid_fea):
        ret = {}
        fea1, fea2, fea3, fea4, fea5 = mid_fea['shortcut']
        x = self.layer1(x) + fea5
        x = self.layer2(x) + fea4
        x_os8 = self.refine_OS8(x)
        ### x [bn, 128, h/8, w/8]
        ### x_os8 [bn, 2, h/8, w/8]

        x = self.layer3(x) + fea3
        x_os4 = self.refine_OS4(x)
        ### x [bn, 128, h/4, w/4] 
        ### x_os4 [bn, 2, h/4, w/4]

        x = self.layer4(x) + fea2
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.leaky_relu(x) + fea1
        x_os1 = self.refine_OS1(x)
        ### x [bn, 32, h, w] 
        ### x_os1 [bn, 2, h, w]

        x_os4 = F.interpolate(x_os4, scale_factor=4.0, mode='bilinear', align_corners=False)
        x_os8 = F.interpolate(x_os8, scale_factor=8.0, mode='bilinear', align_corners=False)
        
        x_os1 = (torch.tanh(x_os1) + 1.0) / 2.0
        x_os4 = (torch.tanh(x_os4) + 1.0) / 2.0
        x_os8 = (torch.tanh(x_os8) + 1.0) / 2.0

        ret['alpha_os1'] = x_os1
        ret['alpha_os4'] = x_os4
        ret['alpha_os8'] = x_os8

        return ret
