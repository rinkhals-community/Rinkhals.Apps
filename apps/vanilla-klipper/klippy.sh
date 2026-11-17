. /useremain/rinkhals/.current/tools.sh
APP_ROOT=$(dirname $(realpath $0))

# Activate Python venv
python -m venv --without-pip $APP_ROOT
. bin/activate

# Prepare configuration
CONFIG_SOURCE=printer_${KOBRA_MODEL_CODE}.cfg
ACE_CONFIG_SOURCE=ace_${KOBRA_MODEL_CODE}.cfg
if [ ! -f $CONFIG_SOURCE ]; then
    echo "ERROR: printer config not found for model $KOBRA_MODEL_CODE: $CONFIG_SOURCE" >&2
    exit 1
fi

CONFIG_DESTINATION=/userdata/app/gk/printer_data/config/printer.klipper.cfg
if [ ! -f $CONFIG_DESTINATION ]; then
    cp $CONFIG_SOURCE $CONFIG_DESTINATION
fi

#Copy printer specific ACE config if there is one
if [ -f $ACE_CONFIG_SOURCE ]; then
    ACE_CONFIG_DESTINATION=/userdata/app/gk/printer_data/config/$ACE_CONFIG_SOURCE
    GENERIC_MACROS_DESTINATION=/userdata/app/gk/printer_data/config/printer_generic_macros.cfg
    ACE_MACROS_GENERIC_DESTINATION=/userdata/app/gk/printer_data/config/ace_macros_generic.cfg

    if [ ! -f "$ACE_CONFIG_DESTINATION" ]; then
        cp "$ACE_CONFIG_SOURCE" "$ACE_CONFIG_DESTINATION"
    fi

    if [ ! -f "$GENERIC_MACROS_DESTINATION" ]; then
        cp printer_generic_macros.cfg "$GENERIC_MACROS_DESTINATION"
    fi

    if [ ! -f "$ACE_MACROS_GENERIC_DESTINATION" ]; then
        cp ace_macros_generic.cfg "$ACE_MACROS_GENERIC_DESTINATION"
    fi
fi

MAINSAIL_DESTINATION=/userdata/app/gk/printer_data/config/mainsail.cfg
if [ ! -f "$MAINSAIL_DESTINATION" ]; then
    cp mainsail.cfg "$MAINSAIL_DESTINATION"
fi

# Start Klippy
cd klippy

# Log where Moonraker serves logs from, so KLIPPY.LOG in Mainsail and Fluidd
# works (rinkhals-community/Rinkhals#151). That's flash, not the /tmp RAM disk,
# and klippy.patch keeps only one rotated copy so it can't grow unbounded.
KLIPPY_LOG=/userdata/app/gk/printer_data/logs/klippy.log
mkdir -p "$(dirname "$KLIPPY_LOG")"
nice -n -20 python -m klippy -a /tmp/unix_uds1 -l "$KLIPPY_LOG" "$CONFIG_DESTINATION" >/dev/null 2>&1 &
assert_by_name klippy
