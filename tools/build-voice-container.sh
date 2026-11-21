#!/usr/bin/env bash
set -euo pipefail
commit=${1:?source commit required}
cd /tmp
root="techo5-$commit"
tar -xzf /src.tar.gz "$root/echod" "$root/LICENSE" "$root/NOTICE"
cd "$root/echod"
export GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 GOTOOLCHAIN=local GOWORK=off
sha256sum go.mod go.sum >/tmp/modules.sha256
go mod download
go mod verify
sha256sum -c /tmp/modules.sha256
export GOPROXY=off
# Set the source identity explicitly. This is a development build.
layout=github.com/HuskerMinion/techo5/echod/internal/layout
go build -p 4 -mod=readonly -trimpath -buildvcs=false -tags dot \
    -ldflags="-s -w -X $layout.GitCommit=${commit:0:12} -X ${layout}.BuildDate=2026-11-21T16:56:51Z" \
    -o /out/techo5-echod ./cmd/echod
go version -m /out/techo5-echod >/out/BUILDINFO
mkdir -p /out/licenses
cp ../LICENSE /out/licenses/TECHO5-LICENSE
cp ../NOTICE /out/licenses/TECHO5-NOTICE
cp LICENSE /out/licenses/EchoLocal-LICENSE
cp NOTICE /out/licenses/echod-NOTICE
cp internal/hardware/speaker/sounds/LICENSE.md /out/licenses/speaker-sounds-LICENSE.md
cp "$(go env GOROOT)/LICENSE" /out/licenses/Go-LICENSE
cp go.mod go.sum /out/licenses/
# Copy notices from the Go packages actually linked, including a package's
# license when it differs from the enclosing module's.
go list -deps -mod=readonly -tags dot -f '{{if .Module}}{{.Module.Path}}|{{.Module.Dir}}|{{.Dir}}{{end}}' ./cmd/echod |
    sort -u >/tmp/voice-modules
while IFS='|' read -r module base package; do
    [[ -n "$module" ]] || continue
    dir=$package
    while [[ "$dir" == "$base" || "$dir" == "$base/"* ]]; do
        rel=${dir#"$base"}
        target="/out/licenses/modules/$module$rel"
        for notice in "$dir"/{LICENSE,LICENCE,NOTICE,COPYING,PATENTS}{,.*,-*}; do
            [[ -f "$notice" ]] || continue
            mkdir -p "$target"
            destination="$target/${notice##*/}"
            [[ -f "$destination" ]] || install -m 0644 "$notice" "$destination"
        done
        [[ "$dir" != "$base" ]] || break
        dir=${dir%/*}
    done
done </tmp/voice-modules
sha256sum -c /tmp/modules.sha256
(cd /out && sha256sum techo5-echod >SHA256SUMS)
chown -R "$OUTPUT_UID:$OUTPUT_GID" /out
