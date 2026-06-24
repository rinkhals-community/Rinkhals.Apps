# Token authentication introduction

Token, also known as a dynamic key, is used by the Agora background to perform dynamic permission checks on app users, and is used to dynamically authenticate app users when joining channels in production environments and other environments with higher security requirements.

During the evaluation, integration, and testing phases of your project, you can only use App ID authentication. However, in order to make your product more secure, more compliant, and less susceptible to malicious interference, it is recommended to upgrade to the Token authentication scheme before the project goes live. For detailed methods, please refer to the document [Verify User Permissions](https://docs.agora.io/en/Agora%20Platform/token)

## Please pay special attention to the following:

The generation of Token depends on four elements: App ID, App Certificate, Channel Name (channel name) and User ID (user ID). When using a token, the `channel_name` passed in `agora_rtc_join_channel` and the uid passed in `agora_rtc_init`must be exactly the same as the Channel Name and User ID used when generating the token, otherwise token verification will fail.