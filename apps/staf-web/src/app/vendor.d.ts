// Bootstrap 5.3.8's modal, imported on its own (the package ships no types).
declare module 'bootstrap/js/dist/modal' {
  export default class Modal {
    constructor(element: Element, options?: Record<string, unknown>)
    show(): void
    hide(): void
    dispose(): void
  }
}

// plotly.js 3.6.0's basic bundle (scatter traces), the cross-section plot's; loaded on demand.
declare module 'plotly.js-basic-dist-min'
