#!/bin/bash
# Convert all .opus files under a given directory to MP3, then delete the originals
TARGET="${1:-/data/downloads}"
echo "Scanning for .opus files under: $TARGET"
found=0
converted=0
while IFS= read -r -d '' f; do
    found=$((found+1))
    out="${f%.opus}.mp3"
    echo "  Converting: $f"
    if ffmpeg -i "$f" -codec:a libmp3lame -qscale:a 0 "$out" -y -loglevel error; then
        rm "$f"
        echo "  Done: $out"
        converted=$((converted+1))
    else
        echo "  FAILED: $f"
    fi
done < <(find "$TARGET" -name "*.opus" -print0)
echo "Converted $converted / $found opus files."
