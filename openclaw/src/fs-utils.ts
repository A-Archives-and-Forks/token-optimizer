/**
 * Filesystem helpers shared across the checkpoint/continuity write paths.
 */

import * as fs from "fs";

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
export function writeFileNoFollow(filePath: string, data: string, mode: number): void {
  const fd = fs.openSync(
    filePath,
    // eslint-disable-next-line no-bitwise
    fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_NOFOLLOW,
    mode
  );
  try {
    fs.writeFileSync(fd, data, "utf-8");
  } finally {
    fs.closeSync(fd);
  }
}

/**
 * Append to a file while refusing to follow a symlink at the final path
 * component. Same TOCTOU-narrowing and platform caveats as {@link writeFileNoFollow};
 * O_APPEND preserves prior entries (used for JSONL manifests/event logs).
 */
export function appendFileNoFollow(filePath: string, data: string, mode: number): void {
  const fd = fs.openSync(
    filePath,
    // eslint-disable-next-line no-bitwise
    fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_APPEND | fs.constants.O_NOFOLLOW,
    mode
  );
  try {
    fs.writeSync(fd, data);
  } finally {
    fs.closeSync(fd);
  }
}
