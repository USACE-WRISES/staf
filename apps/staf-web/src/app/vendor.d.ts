// Bootstrap 5.3.8's modal, imported on its own (the package ships no types).
declare module 'bootstrap/js/dist/modal' {
  export default class Modal {
    constructor(element: Element, options?: Record<string, unknown>)
    show(): void
    hide(): void
    dispose(): void
  }
}
