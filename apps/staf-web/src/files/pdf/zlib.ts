// zlib.compress(data): the zlib stream at the default level (pako, whose deflate equals zlib's).
import { deflate } from 'pako'

export const deflateZlib = (data: Uint8Array): Uint8Array => deflate(data, { level: 6 })
