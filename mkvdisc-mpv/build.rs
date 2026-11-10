use std::env;
use std::path::PathBuf;

fn main() {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let sdk_dir = manifest_dir.parent().unwrap().join("mpv-sdk");
    let include_dir = sdk_dir.join("include");
    let header_path = include_dir.join("mpv").join("client.h");

    println!("cargo:rerun-if-changed={}", header_path.display());

    let bindings = bindgen::Builder::default()
        .header(header_path.to_str().unwrap())
        .clang_arg(format!("-I{}", include_dir.display()))
        .clang_arg("--target=x86_64-pc-windows-msvc")
        .allowlist_type("mpv_.*")
        .allowlist_var("MPV_.*")
        .prepend_enum_name(false)
        .layout_tests(false)
        .generate()
        .expect("Unable to generate bindings from mpv-sdk/include/mpv/client.h");

    let out_path = PathBuf::from(env::var("OUT_DIR").unwrap());
    bindings
        .write_to_file(out_path.join("mpv_bindings.rs"))
        .expect("Couldn't write mpv_bindings.rs!");
}
