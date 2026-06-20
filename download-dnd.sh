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

    # state.json holds live progress and is gitignored; seed it from the
    # tracked initial state on first run (or after a campaign reset/delete).
    if [ ! -f "${DND_PATH}/state.json" ] && [ -f "${DND_PATH}/state.initial.json" ]; then
        echo "No state.json found; seeding new campaign from state.initial.json."
        cp "${DND_PATH}/state.initial.json" "${DND_PATH}/state.json"
    fi

    if [ -z "${KINDLE_EMAIL_ADDRESS}" ] && { [ -z "${TELEGRAM_BOT_TOKEN}" ] || [ -z "${TELEGRAM_CHAT_ID}" ]; }; then
        echo "Neither Kindle email nor Telegram details are set. Nothing to deliver. Exiting."
        exit 1
    fi
}

function advance_game() {
    # Pull the newest marked-up reply, interpret it, and apply it to state.json.
    # Any failure (or no reply) leaves state untouched: render then shows the
    # current page again. The actual email send stays gated on --disable-send.
    if [ -z "${GEMINI_API_KEY}" ]; then
        echo "GEMINI_API_KEY not set; skipping move interpretation (render current state)."
        return
    fi

    source /opt/venv/bin/activate

    local ingest_json status
    ingest_json=$(python3 "${DND_PATH}/ingest_inbox.py") || {
        echo "Ingest failed; rendering current state."; deactivate; return; }
    status=$(echo "${ingest_json}" | jq -r '.status // "error"')

    if [ "${status}" != "new_reply" ]; then
        echo "No new reply (${status}); advancing nothing, re-rendering current page."
        deactivate; return
    fi

    local att msgid move_file
    att=$(echo "${ingest_json}" | jq -r '.attachment_path')
    msgid=$(echo "${ingest_json}" | jq -r '.message_id')
    move_file=$(mktemp /crosswords/tmp/dnd-move-XXXXXX.json)

    echo "Interpreting reply ${msgid} ..."
    if python3 "${DND_PATH}/process_vision.py" "${att}" > "${move_file}"; then
        echo "Applying move to campaign state ..."
        if python3 "${DND_PATH}/engine.py" "${move_file}" --message-id "${msgid}"; then
            MOVE_APPLIED=true  # global: tells --poll mode a page is worth sending
        else
            echo "Engine failed; state unchanged."
        fi
    else
        echo "Vision interpretation failed; state unchanged."
    fi

    rm -f "${att}" "${move_file}"
    deactivate
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

    # Unlike the news script, the D&D page honours --disable-send for Telegram
    # too, so dev/iteration runs don't spam the chat.
    if [ -n "${DISABLE_SEND}" ]; then
        echo "Sending disabled. Will not send ${pdf_name} to Telegram."
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
            --poll)
                # Inbox poll: only render/send when a reply actually advances the game.
                POLL_MODE=true
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
advance_game

# In --poll mode, stay silent unless a move was applied this run (avoids
# re-sending the same page every poll). Normal/daily runs always send.
if [ -n "${POLL_MODE}" ] && [ -z "${MOVE_APPLIED}" ]; then
    echo "Poll: no new move applied; not rendering or sending."
else
    render_page
    send_to_kindle "${OUTPUT_PDF_PATH}"
    send_to_telegram "${OUTPUT_PDF_PATH}"
fi

echo -e "-----------------D&D PAGE SENDER FINISHED-----------------
"
