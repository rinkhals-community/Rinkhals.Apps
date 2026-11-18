#!/bin/sh

function beep() {
    echo 1 > /sys/class/pwm/pwmchip0/pwm0/enable
    usleep $(($1 * 1000))
    echo 0 > /sys/class/pwm/pwmchip0/pwm0/enable
}

UPDATE_PATH="/useremain/update_swu"

# Identify app to be installed
APP_ROOT=$(find $UPDATE_PATH -type d -mindepth 1 -maxdepth 1 2> /dev/null)
APP=$(basename $APP_ROOT)

chmod +x $APP_ROOT/app.sh
APP_VERSION=$($APP_ROOT/app.sh version)

# App dependencies: "depends" in app.json lists the apps this one can't work
# without. Refuse to install until they are all installed, the same rule
# Rinkhals applies in its app store (rinkhals-community/Rinkhals#246). This
# script also runs on Rinkhals versions from before that, and from a USB stick
# with nothing else loaded, so it reads the list itself and doesn't use jq.
USER_APPS=/useremain/home/rinkhals/apps
BUILTIN_APPS=/useremain/rinkhals/.current/home/rinkhals/apps

is_app_installed() {
    for APPS in $USER_APPS $BUILTIN_APPS; do
        if [ -f $APPS/$1/app.sh ] && [ -f $APPS/$1/app.json ]; then
            return 0
        fi
    done
    return 1
}

DEPENDS=$(sed '/^[[:space:]]*\/\//d' $APP_ROOT/app.json 2> /dev/null | tr -d '\n\r' \
    | sed -n 's/.*"depends"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p' | tr -d '" \t' | tr ',' ' ')

# An entry can name alternatives separated by |: any one of them will do
MISSING=""
for DEPEND in $DEPENDS; do
    MET=0
    for ALTERNATIVE in $(echo $DEPEND | tr '|' ' '); do
        is_app_installed $ALTERNATIVE && MET=1
    done
    if [ "$MET" = "0" ]; then
        MISSING="$MISSING${MISSING:+, }$(echo $DEPEND | sed 's/|/ or /g')"
    fi
done

if [ -n "$MISSING" ]; then
    MESSAGE="Can't install $APP. It needs these apps installed first: $MISSING"
    echo "$MESSAGE"

    # Installing from a USB stick, there is no screen to say so on: leave a
    # note on the stick, and beep three times instead of the usual once
    if grep -q ' /mnt/udisk ' /proc/mounts 2> /dev/null; then
        echo "$MESSAGE" >> /mnt/udisk/rinkhals-app-install.log
    fi
    for i in 1 2 3; do
        beep 150
        usleep 150000
    done

    exit 1
fi

# Copy to final path
DESTINATION_ROOT=/useremain/home/rinkhals/apps/$APP

# TODO: Check if we are downgrading
# if [ -f $DESTINATION_ROOT/app.sh ]; then
#     DESTINATION_VERSION=$($DESTINATION_ROOT/app.sh version)
#     if [ "$APP_VERSION" \> "$DESTINATION_VERSION" ]; then
#     fi
# fi

mkdir -p $DESTINATION_ROOT
cp -r $APP_ROOT/* $DESTINATION_ROOT/

# Beep to notify completion
beep 500
