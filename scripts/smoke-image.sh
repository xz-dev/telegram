#!/bin/sh
set -eu
engine=${CONTAINER_ENGINE:-docker}
image=$1
expected_binary_sha=$2
actual=$($engine run --rm --entrypoint sha256sum "$image" /usr/bin/mautrix-telegram)
test "${actual%% *}" = "$expected_binary_sha"
$engine run --rm --entrypoint /usr/bin/mautrix-telegram "$image" --version
$engine run --rm --entrypoint sh "$image" -ec '
  command -v ffmpeg
  command -v lottieconverter
  test -x /docker-run.sh
  printf "%s" '\''{"v":"5.7.4","fr":25,"ip":0,"op":2,"w":32,"h":32,"nm":"smoke","ddd":0,"assets":[],"layers":[]}'\'' |
    lottieconverter - /tmp/lottie.gif gif 32x32 25
  test -s /tmp/lottie.gif
  ffmpeg -v error -f lavfi -i color=red:s=32x32:r=2 -t 1 -c:v libvpx-vp9 /tmp/sticker.webm
  ffmpeg -v error -c:v libvpx-vp9 -i /tmp/sticker.webm -vf "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" /tmp/sticker.gif
  test -s /tmp/sticker.gif
'
