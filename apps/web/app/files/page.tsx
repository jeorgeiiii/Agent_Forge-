"use client";
/**
 * Files page - /files
 * Upload files for workflows to use, download or delete them, and copy the
 * reference a block needs ({"$file": ...}).
 */
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, deleteFile, fileDownloadUrl, listFiles, uploadFile, type StoredFile } from "@/lib/api";
import { PAGE_HELP, formatBytes } from "@/lib/page-help";
import { useSession } from "@/lib/session";
import RoleHelpBox from "../role-help-box";

export default function FilesPage() {
  const router = useRouter();
  const { status, workspace } = useSession();
  const workspaceId = status === "signed-in" ? workspace?.id ?? null : null;
  const canEdit = status !== "signed-in" || workspace?.role !== "viewer";

  const [files, setFiles] = useState<StoredFile[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  /** Loads the file list again (after any change). */
  const load = useCallback(() => {
    listFiles(workspaceId)
      .then(setFiles)
      .catch((reason) => {
        if (reason instanceof ApiError && reason.status === 401) return router.replace("/login?next=/files");
        setError(reason instanceof Error ? reason.message : "Could not load files.");
        setFiles([]);
      });
  }, [workspaceId, router]);

  useEffect(() => { if (status !== "loading") load(); }, [status, load]);

  /** Uploads every file picked in the file box, one after another. */
  async function upload(list: FileList | null) {
    if (!list || list.length === 0) return;
    setUploading(true);
    setError("");
    try {
      for (const file of Array.from(list)) await uploadFile(file, workspaceId);
      setNotice(list.length === 1 ? `Uploaded "${list[0].name}".` : `Uploaded ${list.length} files.`);
      load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function remove(file: StoredFile) {
    if (!window.confirm(`Delete "${file.name}"? Workflows that use it won't find it any more.`)) return;
    try { await deleteFile(file.id); setNotice(`Deleted "${file.name}".`); load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not delete."); }
  }

  /** Copies the reference to paste into a block setting (e.g. "Send a saved file"). */
  async function copyRef(file: StoredFile) {
    try { await navigator.clipboard.writeText(JSON.stringify(file.ref)); setNotice(`Copied the reference for "${file.name}" - paste it into a block's file setting.`); }
    catch { setError("Could not copy - your browser blocked the clipboard."); }
  }

  return (
    <main className="container">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>Files</h1>
          <p className="muted">{workspace ? `For the workspace "${workspace.name}"` : "Your files"}</p>
        </div>
        {canEdit && (
          <label className={`btn primary${uploading ? " disabled" : ""}`}>
            {uploading ? "Uploading…" : "⬆ Upload files"}
            <input ref={inputRef} type="file" multiple hidden disabled={uploading} onChange={(e) => void upload(e.target.files)} />
          </label>
        )}
      </div>
      <RoleHelpBox help={PAGE_HELP.files}
                   extra={<p className="muted help-small">Up to 25 MB each. Programs (.exe, .bat, scripts) are blocked for safety.</p>} />

      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="ge-ok" role="status">{notice}</p>}

      <div className="list">
        {files === null && <p className="muted">Loading…</p>}
        {files?.length === 0 && <p className="muted">No files yet. Press ⬆ Upload files to add one.</p>}
        {files?.map((file) => (
          <div className="card" key={file.id}>
            <div>
              <strong>📄 {file.name}</strong>
              <div className="muted">
                {formatBytes(file.size)} · {file.mime} · {new Date(file.created_at).toLocaleString()}
                {file.run_id ? ` · made by run #${file.run_id}` : ""}
                {file.status !== "ready" ? ` · ${file.status}` : ""}
              </div>
            </div>
            <div className="row-actions">
              {file.status === "ready" && <a className="btn" href={fileDownloadUrl(file.id)} download={file.name}>Download</a>}
              <button className="btn" type="button" onClick={() => void copyRef(file)} title="Copy what a block needs to use this file">Copy reference</button>
              {canEdit && <button className="btn danger" type="button" onClick={() => void remove(file)}>Delete</button>}
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
