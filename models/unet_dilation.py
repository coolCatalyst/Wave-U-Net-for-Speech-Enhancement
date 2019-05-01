import torch
import torch.nn as nn
import torch.nn.functional as F

from utils.utils import calculate_same_padding


class DownSamplingLayer(nn.Module):
    def __init__(self, channel_in, channel_out, dilation=1, kernel_size=15, stride=1, padding=7):
        super(DownSamplingLayer, self).__init__()
        self.main = nn.Sequential(
            nn.Conv1d(channel_in, channel_out, kernel_size=kernel_size,
                      stride=stride, padding=padding, dilation=dilation),
            nn.BatchNorm1d(channel_out),
            nn.LeakyReLU(negative_slope=0.1)
        )

    def forward(self, ipt):
        return self.main(ipt)

class UpSamplingLayer(nn.Module):
    def __init__(self, channel_in, channel_out, dilation=1, kernel_size=5, stride=1, padding=2):
        super(UpSamplingLayer, self).__init__()
        self.main = nn.Sequential(
            nn.Conv1d(channel_in, channel_out, kernel_size=kernel_size,
                      stride=stride, padding=padding, dilation=dilation),
            nn.BatchNorm1d(channel_out),
            nn.LeakyReLU(negative_slope=0.1, inplace=True),
        )

    def forward(self, ipt):
        return self.main(ipt)

class UNet(nn.Module):
    def __init__(self,
                 n_layers=12,
                 channels_interval=24,
                 kernel_size_in_encoder=15,
                 kernel_size_in_decoder=5,
                 dilation_in_encoder=None,
                 dilation_in_decoder=None):
        super(UNet, self).__init__()
        #TODO 为什么调换 kernel_size_in_encoder 与 kernel_size_in_decoder 会使参数量激增 400 W

        if dilation_in_encoder:
            print(f"当前模型将在 **降采样层** 中使用膨胀卷积：{dilation_in_encoder}")

        if dilation_in_decoder:
            print(f"当前模型将在 **升采样层** 中使用膨胀卷积：{dilation_in_decoder}")

        self.n_layers = n_layers
        self.channels_interval = channels_interval
        encoder_in_channels_list = [1] + [i * self.channels_interval for i in range(1, self.n_layers)]
        encoder_out_channels_list = [i * self.channels_interval for i in range(1, self.n_layers + 1)]

        #          1    => 2    => 3    => 4    => 5    => 6   => 7   => 8   => 9  => 10 => 11 =>12
        # 16384 => 8192 => 4096 => 2048 => 1024 => 512 => 256 => 128 => 64 => 32 => 16 =>  8 => 4
        self.encoder = nn.ModuleList()
        for i in range(self.n_layers):
            dilated_rate = None
            if (i + 1) in dilation_in_encoder["layers"]:
                index_in_dilated_rates = dilation_in_encoder["layers"].index(i + 1)
                dilated_rate = dilation_in_encoder["dilated_rates"][index_in_dilated_rates]

            self.encoder.append(
                DownSamplingLayer(
                    channel_in=encoder_in_channels_list[i],
                    channel_out=encoder_out_channels_list[i],
                    kernel_size=kernel_size_in_encoder,
                    dilation=dilated_rate if dilated_rate else 1,
                    padding=calculate_same_padding(
                        l_in=encoder_in_channels_list[i],
                        kernel_size=kernel_size_in_encoder,
                        stride=1,
                        dilation=dilated_rate if dilated_rate else 1
                    ),
                )
            )

        self.middle = nn.Sequential(
            nn.Conv1d(self.n_layers * self.channels_interval, self.n_layers * self.channels_interval, 15, stride=1,
                      padding=7),
            nn.BatchNorm1d(self.n_layers * self.channels_interval),
            nn.LeakyReLU(negative_slope=0.1, inplace=True)
        )

        decoder_in_channels_list = [(2 * i + 1) * self.channels_interval for i in range(1, self.n_layers)] + [
            2 * self.n_layers * self.channels_interval]
        decoder_in_channels_list = decoder_in_channels_list[::-1]
        decoder_out_channels_list = encoder_out_channels_list[::-1]
        self.decoder = nn.ModuleList()


        for i in range(self.n_layers):
            dilated_rate = None

            if (i + 1) in dilation_in_decoder["layers"]:
                index_in_dilated_rates = dilation_in_decoder["layers"].index(i + 1)
                dilated_rate = dilation_in_decoder["dilated_rates"][index_in_dilated_rates]

            self.decoder.append(
                UpSamplingLayer(
                    channel_in=decoder_in_channels_list[i],
                    channel_out=decoder_out_channels_list[i],
                    kernel_size=kernel_size_in_decoder,
                    dilation=dilated_rate if dilated_rate else 1,
                    padding=calculate_same_padding(
                        l_in=encoder_in_channels_list[i],
                        kernel_size=kernel_size_in_decoder,
                        stride=1,
                        dilation=dilated_rate if dilated_rate else 1
                    ),
                )
            )

        self.out = nn.Sequential(
            nn.Conv1d(1 + self.channels_interval, 1, kernel_size=1, stride=1),
            nn.Tanh()
        )

    def forward(self, ipt):
        tmp = []
        o = ipt

        # Up Sampling
        for i in range(self.n_layers):
            o = self.encoder[i](o)
            tmp.append(o)
            # [batch_size, T // 2, channels]
            o = o[:, :, ::2]

        o = self.middle(o)

        # Down Sampling
        for i in range(self.n_layers):
            # [batch_size, T * 2, channels]
            o = F.interpolate(o, scale_factor=2, mode="linear", align_corners=True)
            # Skip Connection
            o = torch.cat([o, tmp[self.n_layers - i - 1]], dim=1)
            o = self.decoder[i](o)

        o = torch.cat([o, ipt], dim=1)
        o = self.out(o)
        return o


#             n_layers = 12, channels_interval = 24
#             UpSamplingLayer(288 + 288, 288),
#             UpSamplingLayer(264 + 288, 264), # 同水平层的降采样后维度为 264
#             UpSamplingLayer(240 + 264, 240),
#
#             UpSamplingLayer(216 + 240, 216),
#             UpSamplingLayer(192 + 216, 192),
#             UpSamplingLayer(168 + 192, 168),
#
#             UpSamplingLayer(144 + 168, 144),
#             UpSamplingLayer(120 + 144, 120),
#             UpSamplingLayer(96 + 120, 96),
#
#             UpSamplingLayer(72 + 96, 72),
#             UpSamplingLayer(48 + 72, 48),
#             UpSamplingLayer(24 + 48, 24),
