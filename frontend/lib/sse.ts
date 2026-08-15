/**
 * Server-Sent Events parsing.
 *
 * Kept as a pure, incremental parser because the hard part is not the protocol
 * but the chunk boundaries: a network chunk can split a frame anywhere,
 * including mid-JSON or between the `event:` and `data:` lines. Holding the
 * remainder in the parser (rather than assuming chunk == frame) is what makes
 * streaming reliable, and it is fully unit-testable without a server.
 */

export interface SSEFrame {
  event: string;
  data: string;
}

export class SSEParser {
  private buffer = '';

  /** Feed a chunk; get back whatever complete frames it completed. */
  push(chunk: string): SSEFrame[] {
    this.buffer += chunk;
    const frames: SSEFrame[] = [];

    // Frames are separated by a blank line. Normalize CRLF first.
    const normalized = this.buffer.replace(/\r\n/g, '\n');
    const blocks = normalized.split('\n\n');
    // The last block may be incomplete — keep it for the next chunk.
    this.buffer = blocks.pop() ?? '';

    for (const block of blocks) {
      const frame = parseBlock(block);
      if (frame) frames.push(frame);
    }
    return frames;
  }

  /** Flush a trailing frame that arrived without its final blank line. */
  flush(): SSEFrame[] {
    const remaining = this.buffer.trim();
    this.buffer = '';
    if (!remaining) return [];
    const frame = parseBlock(remaining);
    return frame ? [frame] : [];
  }
}

function parseBlock(block: string): SSEFrame | null {
  let event = 'message';
  const dataLines: string[] = [];

  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue; // blank or comment/keep-alive
    const separator = line.indexOf(':');
    const field = separator === -1 ? line : line.slice(0, separator);
    // Per spec a single leading space after the colon is stripped.
    const value = separator === -1 ? '' : line.slice(separator + 1).replace(/^ /, '');

    if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
  }

  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join('\n') };
}

/** Read a fetch Response body as a stream of SSE frames. */
export async function* readSSE(response: Response): AsyncGenerator<SSEFrame> {
  if (!response.body) throw new Error('response has no body to stream');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SSEParser();

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
        yield frame;
      }
    }
    for (const frame of parser.flush()) yield frame;
  } finally {
    reader.releaseLock();
  }
}
