/**
 * One page of a PDF, full screen, no interface at all: the page the kiosk viewer shows for a PDF asset.
 *
 * Loaded by the local viewer from this node's own manager (https://localhost/pdf-view?file=NAME&page=N&fit=contain&bg=...),
 * which is why it can read /uploaded/NAME without a session. The scheduler decides which page is on screen and for how
 * long; this page only draws the one it is asked for. pdf.js is used instead of the browser's own viewer because the
 * latter always draws a toolbar and a side panel that its open parameters can no longer hide.
 */
import * as pdfjs from 'pdfjs-dist/build/pdf.mjs'
import workerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const params = new URLSearchParams(location.search)
const file = params.get('file') ?? ''
const pageNumber = Math.max(1, parseInt(params.get('page') ?? '1', 10) || 1)
const fit = ['contain', 'cover', 'fill'].includes(params.get('fit')) ? params.get('fit') : 'contain'
const background = /^[\w#(),.% ]{1,40}$/.test(params.get('bg') ?? '') ? params.get('bg') : 'black'
const MAX_SIDE = 4096   // a canvas larger than this is refused by some GPUs

document.documentElement.style.cssText = `margin:0;height:100%;background:${background};overflow:hidden`
document.body.style.cssText = `margin:0;height:100%;background:${background};overflow:hidden`

async function render() {
  const pdf = await pdfjs.getDocument({url: '/uploaded/' + encodeURIComponent(file), useSystemFonts: true}).promise
  const page = await pdf.getPage(Math.min(pageNumber, pdf.numPages))
  const base = page.getViewport({scale: 1})
  const ratio = window.devicePixelRatio || 1
  // The canvas is drawn once at the size that suits every fit (the larger of the two scales), then the browser
  // places it with object-fit, exactly as it does for an image asset
  let scale = Math.max(innerWidth * ratio / base.width, innerHeight * ratio / base.height)
  scale = Math.min(scale, MAX_SIDE / Math.max(base.width, base.height))
  const viewport = page.getViewport({scale})
  const canvas = document.createElement('canvas')
  canvas.width = Math.floor(viewport.width)
  canvas.height = Math.floor(viewport.height)
  canvas.style.cssText = `display:block;width:100vw;height:100vh;object-fit:${fit}`
  document.body.appendChild(canvas)
  await page.render({canvasContext: canvas.getContext('2d'), viewport, background: 'rgb(255,255,255)'}).promise
  document.title = `${pageNumber}/${pdf.numPages}`
}

render().catch(error => console.error('Cannot show the PDF', error))
