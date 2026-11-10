use crate::Manifest;
use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::ptr::null_mut;

pub struct MkvDiscEngineContext {
    pub manifest: Manifest,
}

#[repr(C)]
pub struct ValidationResult {
    pub is_valid: bool,
    pub error_message: *mut c_char,
}

#[no_mangle]
pub unsafe extern "C" fn mkvdisc_validate_manifest(json_ptr: *const c_char) -> ValidationResult {
    if json_ptr.is_null() {
        let err = CString::new("Null JSON pointer passed to validator.").unwrap();
        return ValidationResult {
            is_valid: false,
            error_message: err.into_raw(),
        };
    }

    let raw_str = match CStr::from_ptr(json_ptr).to_str() {
        Ok(s) => s,
        Err(_) => {
            let err = CString::new("Invalid UTF-8 string in manifest.").unwrap();
            return ValidationResult {
                is_valid: false,
                error_message: err.into_raw(),
            };
        }
    };

    match Manifest::from_json_str(raw_str) {
        Ok(_) => ValidationResult {
            is_valid: true,
            error_message: null_mut(),
        },
        Err(e) => {
            let err = CString::new(e).unwrap_or_else(|_| CString::new("Validation error").unwrap());
            ValidationResult {
                is_valid: false,
                error_message: err.into_raw(),
            }
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn mkvdisc_free_validation_result(res: ValidationResult) {
    if !res.error_message.is_null() {
        let _ = CString::from_raw(res.error_message);
    }
}

#[no_mangle]
pub unsafe extern "C" fn mkvdisc_engine_create(json_ptr: *const c_char) -> *mut MkvDiscEngineContext {
    if json_ptr.is_null() {
        return null_mut();
    }
    let raw_str = match CStr::from_ptr(json_ptr).to_str() {
        Ok(s) => s,
        Err(_) => return null_mut(),
    };

    match Manifest::from_json_str(raw_str) {
        Ok(manifest) => {
            let ctx = Box::new(MkvDiscEngineContext { manifest });
            Box::into_raw(ctx)
        }
        Err(e) => {
            eprintln!("[mkvdisc FFI] Manifest validation failed: {}", e);
            null_mut()
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn mkvdisc_engine_destroy(ctx: *mut MkvDiscEngineContext) {
    if !ctx.is_null() {
        let _ = Box::from_raw(ctx);
    }
}
