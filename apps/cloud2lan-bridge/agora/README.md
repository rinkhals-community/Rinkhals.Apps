# Introduction to Agora RTSA-Lite SDK

Agora Real-time Streaming Acceleration (RTSA) SDK Lite version, relying on Agora SD-RTN™ (Software Defined Real-time Network), the underlying real-time transmission network self-built by Agora, supports all network-enabled Linux /RTOS devices provide the ability to transmit audio and video streams in real time over the Internet. RTSA makes full use of the global network nodes of Acoustics and intelligent dynamic routing algorithms. At the same time, it supports various combined anti-weak network strategies such as forward error correction, intelligent retransmission, bandwidth prediction, and stream smoothing. Under various uncertain network environments, it still delivers the best stream transmission experience with high connectivity, high real-time and high stability. In addition, the SDK has an extremely small package size and memory footprint, making it suitable for running on typical IoT devices.

# Five Minute Quick Start

We provide a simple sample project (hello_rtsa) to demonstrate how to push audio and video through RTSA-Lite, and watch the effect in real time through the web page.

## 1. Create an Agora account and get an App ID

Before compiling and running the sample project, you first need to obtain the Agora App ID through the following steps:

1. Create a valid [Agora Account](https://console.agora.io/).
2. Log in to the [Agora Console](https://console.agora.io/), click the **Project Management** button on the left navigation bar to enter the project page.
3. On the project page, click the **Create a project** button. Enter the project name in the pop-up dialog box, and select App ID as the Authentication. The newly created project will be displayed on the project page. Agora will automatically assign an App ID to each project as the unique identifier of the project. Copy and save the **App ID** of this project, the App ID will be used later when running the sample.

## 2. Compile `hello_rtsa`

In the Linux x86 environment (such as Ubuntu 18.04), compile hello_rtsa with the following command.

```
$ cd example
$ ./build-x86_64.sh
```

After the compilation is complete, the `out/x86_64/` directory will be created in the current directory, and the `hello_rtsa` executable file will be generated in this directory.

## 3. Run `hello_rtsa`

You can use the following command line, replace `YOUR_APP_ID` with the App ID you obtained in the first step, join the channel named `hello_demo`, and send H.264 video and PCM audio with default parameters. The audio and video sources default to the `send_video.h264` and `send_audio_16k_1ch.pcm` files that come with the SDK package.

```
$ cd out/x86_64
$ ./hello_rtsa -i YOUR_APPID -c hello_demo
```

The parameter `YOUR_APPID` is the App ID you just created.

Please note: If the Authentication is not set to App ID when creating the project, but is set to Token, an error message `Error 105 is captured` will appear at runtime. Please re-create the project and select App ID as Authentication, or refer to the [official document](https://docs.agora.io/en/video-calling/reference/manage-agora-account?platform=android#generate-a-temporary-token) to generates a temporary Token and uses `-t YOUR_TOKEN` to set the Token parameters, and try to run again.

When the print of `Join the channel "hello_demo" successfully` appears on the terminal, it means that the channel has been successfully joined and streaming has started.

## 4. Watch the live video stream
You can open the [Webdemo](https://webdemo.agora.io/agora-web-showcase/examples/Agora-Web-Tutorial-1to1-Web), and enter `YOUR_APPID` in the `App ID` column, enter `hello_demo` in the `Channels` column, click **JOIN**, then you can see the real-time video streaming.

# Porting

In order to make the sample project (`hello_rtsa`) run on the embedded device (usually ARM Linux system), please refer to [Porting Guide](./docs/PORTING.md)

# Integration
During the integration process of the project, you may need to know the following topics

- [About audio codec](./docs/AUDIO_CODEC.md)
- [Bitrate Control](./docs/BITRATE_CONTROL.md)
- [A more secure Token mechanism](./docs/TOKEN.md)


# About License
In order to allow you to experience our sample projects smoothly, and quickly start integrating and testing your own projects, our SDK provides a 90-day free trial period. Note: Please do not delete the **certificate.bin** file in the same directory as `hello_rtsa`, otherwise you will not be able to use the RTSA-Lite SDK for free. After the free period expires, you can no longer use the RTSA-Lite SDK, and the sample project (`hello_rtsa`) will not work either!

Therefore, before the free period expires, please be sure to contact Agora sales (sales@agora.io), purchase a commercial license, and integrate the license activation mechanism in the project. For detailed procedures, please refer to [License Integration Guide](./docs/LICENSE_GUIDE.md)

# Contact us

- If you encounter any problems with the integration, please submit [issue](https://github.com/AgoraIO/Basic-RTSA/issues)