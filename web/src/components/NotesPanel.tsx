import { useEffect, useMemo, useRef, useState } from 'react';

import type { DocumentElement, Note } from '../api/types';
import { hasValidSourceLocation } from '../workspace/sourceTarget';
import type { SourceTarget } from '../workspace/types';

export interface NotesPanelProps {
  paperId: string | null;
  activeSource: SourceTarget | null;
  notes: Note[];
  documentElements: DocumentElement[];
  saveNote: (body: string, elementId?: string, pageNumber?: number) => Promise<Note | null>;
  onSelectSource: (elementId: string) => void;
}

function sourceDescription(source: SourceTarget): string {
  return `Attached to page ${source.pageNumber} ${source.kind}.`;
}

export function NotesPanel({
  paperId,
  activeSource,
  notes,
  documentElements,
  saveNote,
  onSelectSource,
}: NotesPanelProps) {
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [localNotes, setLocalNotes] = useState<Note[]>([]);
  const [uiPaperId, setUiPaperId] = useState<string | null>(paperId);
  const currentPaperId = useRef(paperId);
  const requestId = useRef(0);

  currentPaperId.current = paperId;

  useEffect(() => {
    requestId.current += 1;
    setUiPaperId(paperId);
    setDraft('');
    setPending(false);
    setErrorMessage(null);
    setLocalNotes([]);
  }, [paperId]);

  useEffect(() => () => {
    requestId.current += 1;
  }, []);

  const isCurrentPaper = uiPaperId === paperId;
  const elementsById = useMemo(
    () => new Map(documentElements.map((element) => [element.id, element])),
    [documentElements],
  );
  const visibleNotes = useMemo(() => {
    if (!isCurrentPaper) {
      return [];
    }

    const notesById = new Map(notes.map((note) => [note.id, note]));
    for (const note of localNotes) {
      if (!notesById.has(note.id)) {
        notesById.set(note.id, note);
      }
    }
    return [...notesById.values()];
  }, [isCurrentPaper, localNotes, notes]);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const body = draft.trim();
    if (!body || pending || paperId === null) {
      return;
    }

    const activeRequestId = ++requestId.current;
    const requestPaperId = paperId;
    const elementId = activeSource?.id;
    const pageNumber = activeSource?.pageNumber;
    setPending(true);
    setErrorMessage(null);

    try {
      const note = await saveNote(body, elementId, pageNumber);
      if (
        requestId.current !== activeRequestId
        || currentPaperId.current !== requestPaperId
        || note === null
      ) {
        return;
      }

      setLocalNotes((current) => current.some(({ id }) => id === note.id)
        ? current
        : [...current, note]);
      setDraft('');
    } catch {
      if (requestId.current === activeRequestId && currentPaperId.current === requestPaperId) {
        setErrorMessage('Unable to save this note.');
      }
    } finally {
      if (requestId.current === activeRequestId && currentPaperId.current === requestPaperId) {
        setPending(false);
      }
    }
  };

  return (
    <section className="notes-panel" aria-labelledby="notes-panel-title">
      <header className="notes-panel__header">
        <div>
          <p className="notes-panel__eyebrow">Reading notes</p>
          <h2 id="notes-panel-title">Notes</h2>
        </div>
        <p className="notes-panel__target">
          {activeSource === null
            ? 'No source selected. This note will be unbound.'
            : sourceDescription(activeSource)}
        </p>
      </header>

      <form className="notes-panel__form" onSubmit={submit}>
        <label htmlFor="new-note">New note</label>
        <textarea
          id="new-note"
          value={isCurrentPaper ? draft : ''}
          onChange={(event) => setDraft(event.target.value)}
          disabled={pending || paperId === null}
          rows={4}
        />
        <button type="submit" disabled={pending || draft.trim() === '' || paperId === null}>
          {pending ? 'Saving…' : 'Save note'}
        </button>
      </form>

      {errorMessage !== null && <p className="notes-panel__error" role="alert">{errorMessage}</p>}

      <div className="notes-panel__list" aria-live="polite">
        {visibleNotes.length === 0 ? (
          <p className="notes-panel__empty">No notes for this paper yet.</p>
        ) : visibleNotes.map((note) => {
          const element = note.element_id === null ? undefined : elementsById.get(note.element_id);
          const canJump = element !== undefined && hasValidSourceLocation(element);

          return (
            <article className="notes-panel__note" key={note.id}>
              <p>{note.body}</p>
              {note.element_id !== null && (
                <button
                  type="button"
                  disabled={!canJump}
                  onClick={() => {
                    if (canJump) {
                      onSelectSource(note.element_id!);
                    }
                  }}
                >
                  {canJump
                    ? `Jump to page ${element.page_number} ${element.kind}`
                    : 'Source location unavailable'}
                </button>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
