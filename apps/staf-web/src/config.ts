/**
 * Where the app's data comes from. Deployed (a section of the STAF website,
 * https://usace-wrises.github.io/staf/app/), it reads the Hugging Face mirror of STAF's releases
 * (tools/staf-web-data); the dev server serves the local layout at `/data/`. A page's `?data=` base
 * overrides both.
 */
export const DATA_MIRROR = 'https://huggingface.co/datasets/gtmenichino/staf-data/resolve/main/'

export function dataBase(params: URLSearchParams): string {
  return params.get('data') ?? (import.meta.env.DEV ? `${location.origin}/data/` : DATA_MIRROR)
}
