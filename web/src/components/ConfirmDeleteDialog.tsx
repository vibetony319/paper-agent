import { useEffect, useId, useRef, useState } from 'react';

import { ApiError, paperApi } from '../api/client';
import type { PaperSummary } from '../api/types';

export interface ConfirmDeleteDialogProps {
  paper: PaperSummary | null;
  onCancel: () => void;
  onDeleted: (paperId: string) => void;
}

const DELETE_FAILED_MESSAGE = '删除失败，请重试。';

export function ConfirmDeleteDialog({ paper, onCancel, onDeleted }: ConfirmDeleteDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const [deleting, setDeleting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (paper === null) {
      return;
    }

    restoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const dialog = dialogRef.current;
    if (dialog !== null && !dialog.open) {
      if (typeof dialog.showModal === 'function') {
        dialog.showModal();
      } else {
        dialog.setAttribute('open', '');
      }
    }
    setDeleting(false);
    setErrorMessage(null);

    return () => {
      restoreFocusRef.current?.focus();
      restoreFocusRef.current = null;
    };
  }, [paper]);

  if (paper === null) {
    return null;
  }

  const confirmDeletion = async () => {
    setDeleting(true);
    setErrorMessage(null);
    try {
      await paperApi.deletePaper(paper.id, paper.id);
      onDeleted(paper.id);
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : DELETE_FAILED_MESSAGE);
      setDeleting(false);
    }
  };

  return (
    <dialog
      ref={dialogRef}
      className="confirm-delete-dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!deleting) {
          onCancel();
        }
      }}
    >
      <header className="confirm-delete-dialog__header">
        <h2 id={titleId}>删除论文</h2>
      </header>
      <p>将永久删除《{paper.original_filename}》及其关联数据。</p>
      <p>PDF、解析结果、知识图谱、高亮、笔记和对话将被永久删除。</p>
      {errorMessage !== null && <p role="alert">{errorMessage}</p>}
      <div className="confirm-delete-dialog__actions">
        <button type="button" disabled={deleting} onClick={onCancel}>取消</button>
        <button
          className="confirm-delete-dialog__confirm"
          type="button"
          disabled={deleting}
          onClick={() => void confirmDeletion()}
        >
          {deleting ? '正在删除…' : '确认永久删除'}
        </button>
      </div>
    </dialog>
  );
}
