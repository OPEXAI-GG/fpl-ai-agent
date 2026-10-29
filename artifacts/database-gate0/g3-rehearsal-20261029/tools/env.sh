# G3 environment: Linux system tools only, plus the three labelled stand-ins in tools/
export G3=/mnt/c/Users/bedor/AppData/Local/Temp/claude/C--Users-bedor-OneDrive-Documents-FPL-fpl-ai-agent-1/5f4810dd-2bab-4621-af08-adf1aa533cf6/scratchpad/g3
export PATH="$G3/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export GH="$G3/tools/gh" VERIFY="$G3/tools/verify" FAKE_STORE="$G3/store" FAKE_MODE=ok ZSTD_LOG="$G3/ev/tool-calls.log"
export SCRIPTS="$G3/src51/scripts" LC_ALL=C.UTF-8 TZ=UTC
chmod +x "$G3"/tools/zstd "$G3"/tools/verify "$G3"/tools/gh
