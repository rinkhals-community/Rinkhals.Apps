# RTSA License Introduction
Each online device requires an activated license in order to function properly. Each license has a corresponding validity period that begins from the moment of activation. After activation, a certificate file is generated that is used to verify the validity of the license each time the device is used.

# RTSA License process

## 1. Obtain the customer ID and customer key required by Agora RESTful API
Since the Agora RESTful API needs to be used to activate the license, you need to perform HTTP basic authentication, that is, use the customer ID and customer key provided by Agora to generate an Authorization field (encoded using the Base64 algorithm).

1. Log in to [Agora Console](https://console.agora.io/), click the user name in the upper right corner of the page, and open the RESTful API page in the drop-down list.
2. Click the **Add Key** button. A new customer ID and customer key will be generated in the following page, click the **Submit** button on the right operation bar.
3. After the page displays the message **Created successfully**, click the **Download** button in the corresponding customer key column.
4. Save the downloaded **`key_and_secret.txt`** , which contains the customer ID and customer secret, which will be used when activating the license later. Note: This ID and key have nothing to do with the specific App ID, but only with the account of the sound network.

## 2. Obtain License Key

If you need to purchase a commercial license, please follow Agora official documentation center's procedure.

## 3. License auxiliary tools
When compiling the sample project (`hello_rtsa`) before, you have compiled the License auxiliary tool at the same time: **license_activator**

## 4. License activation
After you have purchased a certain number of commercial licenses, you can activate them. 

Every time you activate, you need to provide a unique identification code for the device, such as the IMEI number of the device. 

After each successful activation, a purchased license will be consumed. When the purchased license quantity is exhausted, the activation operation will fail.

Here is the activation command:

```
$ ./license_activator --appId YOUR_APP_ID --customerKey YOUR_KEY --customerSecret YOUR_SECRET --deviceId YOUR_DEVICE_ID --certOutputDir .
```

Please note:

- The parameter `YOUR_APPID` needs to be replaced with the App ID you created.
- The parameter `YOUR_KEY` needs to be replaced with the key value in the `key_and_secret.txt` file you just saved
- The parameter `YOUR_SECRET` needs to be replaced with the secret value in the `key_and_secret.txt` file you just saved
- The parameter `YOUR_DEVICE_ID` needs to be replaced with the unique identification code of the device, such as the IMEI number of the device. The unique identifier cannot exceed 128 bytes. The unique identification codes of different devices cannot be repeated

After successful activation, **certificate.bin** will be generated in the current directory by default, which is the license certificate bound to this device. This file will be loaded when `hello_rtsa` is running, and the validity of the license certificate will be verified by calling the `agora_rtc_license_verify` interface before initializing the SDK. Only through verification can the SDK run normally.

Afterwards, the device will call the `agora_rtc_license_verify` method to verify the validity of the license certificate each time before running the RTSA-Lite SDK. Only after passing the verification can the SDK initialization be successful.