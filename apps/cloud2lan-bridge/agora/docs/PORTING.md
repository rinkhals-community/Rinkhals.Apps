# Porting Guide

## 1. Modify `example/scripts/toolchain.cmake` to set the path and prefix of the cross-compilation toolchain

For example, if the toolchain you use is ARM's official GNU Toolchain, you can set it like this

```
set(DIR /home/username/toolchain/gcc-arm-8.3-2019.03-x86_64-arm-linux-gnueabihf/bin/)
set(CROSS "arm-linux-gnueabihf-")
```

Note: The location of the toolchain must be set to **absolute path**

## 2. Compile

```
$ cd example
$ ./build-arm.sh
```

After the compilation is completed, the `out/arm/` directory will be created in the current directory, and the `hello_rtsa` executable file will be generated in this directory.

## 3. Run hello_rtsa

Copy the following files to the `/YOUR_DIR` directory of the target device

1. `agora_sdk/lib/libagora-rtc-sdk.so`
2. `example/out/arm/hello_rtsa`
3. `example/out/arm/send_video.h264`
4. `example/out/arm/send_audio_16k_1ch.pcm`
5. `example/out/arm/certificate.bin`

Perform the following operations on the shell interface of the target device

```
$ export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/YOUR_DIR
$ cd /YOUR_DIR
$ ./hello_rtsa -i YOUR_APPID -c hello_demo
```