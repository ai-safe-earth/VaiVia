import { describe, expect, it } from 'vitest';

import { SSEParser, readSSE } from '../lib/sse';

describe('SSEParser', () => {
  it('parses a single complete frame', () => {
    const parser = new SSEParser();
    expect(parser.push('event: token\ndata: {"delta":"hi"}\n\n')).toEqual([
      { event: 'token', data: '{"delta":"hi"}' },
    ]);
  });

  it('parses several frames in one chunk', () => {
    const parser = new SSEParser();
    const frames = parser.push(
      'event: intent\ndata: {"kind":"trail_search"}\n\nevent: token\ndata: {"delta":"a"}\n\n',
    );
    expect(frames.map((f) => f.event)).toEqual(['intent', 'token']);
  });

  it('holds an incomplete frame until the rest arrives', () => {
    const parser = new SSEParser();
    expect(parser.push('event: token\ndata: {"del')).toEqual([]);
    expect(parser.push('ta":"hello"}\n\n')).toEqual([
      { event: 'token', data: '{"delta":"hello"}' },
    ]);
  });

  it('handles a split between the event and data lines', () => {
    const parser = new SSEParser();
    expect(parser.push('event: results\n')).toEqual([]);
    expect(parser.push('data: {"trails":[]}\n\n')).toEqual([
      { event: 'results', data: '{"trails":[]}' },
    ]);
  });

  it('handles a split exactly on the frame separator', () => {
    const parser = new SSEParser();
    expect(parser.push('event: token\ndata: {"delta":"x"}\n')).toEqual([]);
    expect(parser.push('\nevent: done\ndata: {}\n\n').map((f) => f.event)).toEqual([
      'token',
      'done',
    ]);
  });

  it('accepts CRLF line endings', () => {
    const parser = new SSEParser();
    expect(parser.push('event: token\r\ndata: {"delta":"x"}\r\n\r\n')).toEqual([
      { event: 'token', data: '{"delta":"x"}' },
    ]);
  });

  it('defaults the event name to message', () => {
    const parser = new SSEParser();
    expect(parser.push('data: plain\n\n')).toEqual([{ event: 'message', data: 'plain' }]);
  });

  it('ignores comment keep-alive lines', () => {
    const parser = new SSEParser();
    expect(parser.push(': keep-alive\n\n')).toEqual([]);
  });

  it('joins multi-line data fields', () => {
    const parser = new SSEParser();
    expect(parser.push('data: line one\ndata: line two\n\n')).toEqual([
      { event: 'message', data: 'line one\nline two' },
    ]);
  });

  it('flushes a trailing frame that never got its blank line', () => {
    const parser = new SSEParser();
    expect(parser.push('event: done\ndata: {}')).toEqual([]);
    expect(parser.flush()).toEqual([{ event: 'done', data: '{}' }]);
  });

  it('streams tokens one character at a time without loss', () => {
    const parser = new SSEParser();
    const payload = 'event: token\ndata: {"delta":"Lago Loop"}\n\n';
    const frames = payload.split('').flatMap((char) => parser.push(char));
    expect(frames).toEqual([{ event: 'token', data: '{"delta":"Lago Loop"}' }]);
  });
});

describe('readSSE', () => {
  function responseOf(chunks: string[]): Response {
    const encoder = new TextEncoder();
    return new Response(
      new ReadableStream({
        start(controller) {
          for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
          controller.close();
        },
      }),
    );
  }

  it('yields frames across arbitrary chunk boundaries', async () => {
    const response = responseOf([
      'event: conversation\ndata: {"conversation_id":"c1"}\n\nevent: to',
      'ken\ndata: {"delta":"Lago "}\n\nevent: token\ndata: {"del',
      'ta":"Loop"}\n\nevent: done\ndata: {"usage":{}}\n\n',
    ]);

    const seen: string[] = [];
    for await (const frame of readSSE(response)) seen.push(frame.event);
    expect(seen).toEqual(['conversation', 'token', 'token', 'done']);
  });

  it('rejects a response with no body', async () => {
    const empty = new Response(null);
    await expect(async () => {
      for await (const _ of readSSE(empty)) {
        // no frames expected
      }
    }).rejects.toThrow(/no body/);
  });
});
