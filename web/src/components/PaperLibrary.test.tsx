import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { paperApi } from '../api/client';
import { PaperLibrary } from './PaperLibrary';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('uploads a PDF dropped onto the library area', async () => {
  vi.spyOn(paperApi, 'listPapers').mockResolvedValue([]);
  const paper = { id: 'paper-a', original_filename: 'study.pdf', status: 'queued' as const, stage0_status: 'queued' as const, stage1_status: 'queued' as const, error: null };
  const upload = vi.spyOn(paperApi, 'upload').mockResolvedValue(paper);
  const onPaperSelected = vi.fn();
  render(<PaperLibrary activePaperId={null} paperUpdate={null} onPaperSelected={onPaperSelected} onPaperDeleteRequested={vi.fn()} focusHeading={false} />);
  const area = screen.getByText(/也可以将 PDF 拖到这里/).parentElement!;
  const file = new File(['pdf'], 'study.pdf', { type: 'application/pdf' });
  fireEvent.dragEnter(area, { dataTransfer: { types: ['Files'] } });
  expect(area).toHaveClass('paper-library__upload--dragging');
  fireEvent.drop(area, { dataTransfer: { files: [file] } });
  await waitFor(() => expect(upload).toHaveBeenCalledWith(file));
  expect(onPaperSelected).toHaveBeenCalledWith(paper);
  expect(area).not.toHaveClass('paper-library__upload--dragging');
});
