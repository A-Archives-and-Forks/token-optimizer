"use strict";
/**
 * Filesystem helpers shared across the checkpoint/continuity write paths.
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.writeFileNoFollow = writeFileNoFollow;
exports.appendFileNoFollow = appendFileNoFollow;
const fs = __importStar(require("fs"));
/**
 * Write a file while refusing to follow a symlink at the final path component.
 *
 * Callers lstat-check the target directory/path before the write, but a TOCTOU
 * window remains before the file write: another process could swap the target
 * for a symlink. O_NOFOLLOW makes the kernel reject the open with ELOOP if the
 * final component is a symlink at open time.
 *
 * Limitations (narrows the race, does not close it): only the final component
 * is protected (not parent dirs); only symlinks are rejected, not hardlinks;
 * and it is POSIX-only -- fs.constants.O_NOFOLLOW is undefined on Windows and
 * ORs in as 0, so the open still succeeds but keeps only the pre-existing
 * lstat-based protection (no regression).
 */
function writeFileNoFollow(filePath, data, mode) {
    const fd = fs.openSync(filePath, 
    // eslint-disable-next-line no-bitwise
    fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_NOFOLLOW, mode);
    try {
        fs.writeFileSync(fd, data, "utf-8");
    }
    finally {
        fs.closeSync(fd);
    }
}
/**
 * Append to a file while refusing to follow a symlink at the final path
 * component. Same TOCTOU-narrowing and platform caveats as {@link writeFileNoFollow};
 * O_APPEND preserves prior entries (used for JSONL manifests/event logs).
 */
function appendFileNoFollow(filePath, data, mode) {
    const fd = fs.openSync(filePath, 
    // eslint-disable-next-line no-bitwise
    fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_APPEND | fs.constants.O_NOFOLLOW, mode);
    try {
        fs.writeSync(fd, data);
    }
    finally {
        fs.closeSync(fd);
    }
}
//# sourceMappingURL=fs-utils.js.map