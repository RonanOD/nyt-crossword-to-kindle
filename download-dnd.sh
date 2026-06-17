#!/usr/bin/env bash
set -eo pipefail

SCRIPT_PATH="$(dirname "$(readlink -f "$0")")"
DOWNLOADS_INTERNAL_PATH="${SCRIPT_PATH}/downloads"
DND_PATH="${SCRIPT_PATH}/dnd"
OUTPUT_PDF_PATH="${DOWNLOADS_INTERNAL_PATH}/dnd-$(date +'%Y-%m-%d').pdf"

function verify_env_vars() {
    if [ "${DND_ENABLED}" != "true" ]; then
        echo "DND_ENABLED is not 'true'. Skipping D&D page."
        exit 0
    fi

    if [ ! -d "${DOWNLOADS_INTERNAL_PATH}" ]; then
        echo "Downloads directory not present at ${DOWNLOADS_INTERNAL_PATH}. Exiting."
        exit 1
    fi

    if [ ! -f "${DND_PATH}/render_dnd.py" ]; then
        echo "Renderer not found at ${DND_PATH}/render_dnd.py. Exiting."
        exit 1
    fi

    if [ -z "${KINDLE_EMAIL_ADDRESS}" ] && { [ -z "${TELEGRAM_BOT_TOKEN}" ] || [ -z "${TELEGRAM_CHAT_ID}" ]; }; then
        echo "Neither Kindle email nor Telegram details are set. Nothing to deliver. Exiting."
        exit 1
    fi
}

function render_page() {
    echo "Rendering D&D workbook page..."
    source /opt/venv/bin/activate

    local html_content
    html_content=$(python3 "${DND_PATH}/render_dnd.py")

    local html_file
    html_file=$(mktemp /crosswords/tmp/dnd-XXXXXX.html)
    echo "${html_content}" > "${html_file}"

    if [ ! -s "${html_file}" ]; then
        echo "Error: rendered HTML is empty." >&2
        rm -f "${html_file}"
        exit 1
    fi

    echo "Converting D&D page to PDF..."
    weasyprint "${html_file}" "${OUTPUT_PDF_PATH}"
    rm -f "${html_file}"

    deactivate
    echo "Successfully created PDF: ${OUTPUT_PDF_PATH}"
}

function send_to_kindle() {
    local pdf_path="${1}"
    local pdf_name
    pdf_name=$(basename "${pdf_path}")

    if [ -z "${KINDLE_EMAIL_ADDRESS}" ]; then
        echo "Kindle email not set. Skipping Kindle send."
        return
    fi

    if [ -z "${DISABLE_SEND}" ]; then
        echo "Sending ${pdf_name} to Kindle ${KINDLE_EMAIL_ADDRESS}"
        echo "Today's adventure awaits." | mutt -s "Solo Campaign - Today's Page" -a "${pdf_path}" -- "${KINDLE_EMAIL_ADDRESS}"
        echo 'Send successful!'
    else
        echo "Sending disabled. Will not email ${pdf_name}."
    fi
}

function send_to_telegram() {
    local pdf_path="${1}"
    local pdf_name
    pdf_name=$(basename "${pdf_path}")

    if [ -z "${TELEGRAM_BOT_TOKEN}" ] || [ -z "${TELEGRAM_CHAT_ID}" ]; then
        echo "Telegram details not set. Skipping Telegram send."
        return
    fi

    echo "Sending ${pdf_name} to Telegram chat ${TELEGRAM_CHAT_ID}"
    response=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendDocument" \
        -F chat_id="${TELEGRAM_CHAT_ID}" \
        -F document=@"${pdf_path}" \
        -F caption="Today's adventure page 🐉")

    if echo "${response}" | grep -q '"ok":true'; then
        echo "Telegram send successful!"
    else
        echo "Telegram send failed: ${response}"
    fi
}

function parse_flags() {
    while [ $# -gt 0 ]; do
        case $1 in
            -d | --disable-send)
                echo 'Sending disabled. Will only render the D&D page.'
                DISABLE_SEND=true
                ;;
        esac
        shift
    done
}

# BEGIN MAIN EXECUTION
echo -e "
-----------------D&D PAGE SENDER STARTING-----------------"

verify_env_vars
parse_flags "$@"
render_page
send_to_kindle "${OUTPUT_PDF_PATH}"
send_to_telegram "${OUTPUT_PDF_PATH}"

echo -e "-----------------D&D PAGE SENDER FINISHED-----------------
"
