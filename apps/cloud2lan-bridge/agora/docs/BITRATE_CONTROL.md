## Code stream oversending
During the process of device streaming, it is common to encounter fluctuation in network bandwidth. When a decline in upstream bandwidth causes the device's sending bitrate to go unfulfilled, network congestion inevitably occurs, resulting in a large quantity of packet loss which can cause severe stuttering or even complete failure to play received audio and video content.

## How to avoid code stream oversending
In order to prevent network congestion, RTSA Lite dynamically provides suggested target bitrate values in response to changes in network bandwidth, which are then relayed to users through the `on_target_bitrate_changed` callback. 

Users should adjust the encoder's output bitrate in real-time based on the `target_bps` value provided by this callback. As long as the encoder's output bitrate does not exceed the target bitrate, over-delivery during playback can be avoided, greatly reducing the degradation of audio and video quality caused by network congestion.

## How to judge code stream oversending
Exceeding the bitrate limit represents an abnormal state. If the `on_error` callback is registered during SDK initialization (which we strongly recommend for monitoring any potential abnormal states), the `on_error` callback will be triggered in the event of an over-limit bitrate, with an error code of `ERR_SEND_VIDEO_OVER_BANDWIDTH_LIMIT`. 

Typically, when the audio and video bitstream sent by the user significantly exceeds the device's upstream bandwidth, the frames will be cached by the SDK. When the cached audio and video data exceed a certain threshold, the `ERR_SEND_VIDEO_OVER_BANDWIDTH_LIMIT` error is triggered, and all cached audio and video data will be discarded.