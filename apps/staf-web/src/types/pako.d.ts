// pako 2.2.0 (a port of zlib 1.2.8): the raw deflate the zip writer uses. Its output equals Python's
// zlib 1.3.1 at the same settings (checked on every part of the three tools' calculator templates).
declare module 'pako' {
  export interface DeflateOptions { level?: number, windowBits?: number, memLevel?: number, strategy?: number }
  export function deflateRaw(data: Uint8Array, options?: DeflateOptions): Uint8Array
  export function deflate(data: Uint8Array, options?: DeflateOptions): Uint8Array
  export function inflateRaw(data: Uint8Array): Uint8Array
}
