/**
 * Filesystem helpers shared across the checkpoint/continuity write paths.
 */
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
export declare function writeFileNoFollow(filePath: string, data: string, mode: number): void;
/**
 * Append to a file while refusing to follow a symlink at the final path
 * component. Same TOCTOU-narrowing and platform caveats as {@link writeFileNoFollow};
 * O_APPEND preserves prior entries (used for JSONL manifests/event logs).
 */
export declare function appendFileNoFollow(filePath: string, data: string, mode: number): void;
//# sourceMappingURL=fs-utils.d.ts.map