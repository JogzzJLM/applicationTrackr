#!/bin/bash
set -euo pipefail
source_dir="$(cd "$(dirname "$0")" && pwd)"
build_root="$(mktemp -d /tmp/applicationtrackr-safari.XXXXXX)"
xcrun safari-web-extension-converter "$source_dir" --project-location "$build_root/project" --app-name 'ApplicationTrackr Helper' --bundle-identifier com.applicationtrackr.browserhelper --macos-only --swift --no-open --no-prompt --force
project="$build_root/project/ApplicationTrackr Helper/ApplicationTrackr Helper.xcodeproj"
# Some converter versions generate an inconsistent containing-app identifier.
python3 - "$project/project.pbxproj" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
p.write_text(p.read_text().replace('"com.applicationtrackr.ApplicationTrackr-Helper"', 'com.applicationtrackr.browserhelper'))
PY
xcodebuild -project "$project" -scheme 'ApplicationTrackr Helper' -configuration Debug -derivedDataPath "$build_root/derived" CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM= build
printf '\nBuilt helper: %s\n' "$build_root/derived/Build/Products/Debug/ApplicationTrackr Helper.app"
