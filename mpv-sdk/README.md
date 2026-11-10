# mpv C API headers

The headers in `include/mpv/` are the mpv client API headers used by
`mkvdisc-mpv/build.rs` to generate Rust FFI bindings. They are distributed
with their upstream copyright and ISC license notices intact.

Only the headers required to build the plugin are included here. The mpv
runtime DLLs and import libraries used for local playback are intentionally
not distributed in this repository.
