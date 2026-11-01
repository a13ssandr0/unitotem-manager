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
const SAMPLES = 24          // the picture is checked on a SAMPLES x SAMPLES grid of pixels
const REFERENCE_SIDE = 1024 // the small drawing that tells a blank page from a canvas that did not draw

document.documentElement.style.cssText = `margin:0;height:100%;background:${background};overflow:hidden`
document.body.style.cssText = `margin:0;height:100%;background:${background};overflow:hidden`

async function render() {
  // The standard fonts and the character maps come with the page (see vite.config.mjs), the other fonts a PDF does not carry fall back to the system's
  const pdf = await pdfjs.getDocument({
    url: '/uploaded/' + encodeURIComponent(file), useSystemFonts: true,
    standardFontDataUrl: '/assets/pdfjs/standard_fonts/', cMapUrl: '/assets/pdfjs/cmaps/', cMapPacked: true,
  }).promise
  const page = await pdf.getPage(Math.min(pageNumber, pdf.numPages))
  const base = page.getViewport({scale: 1})
  const ratio = window.devicePixelRatio || 1
  // The canvas is drawn once at the size that suits every fit (the larger of the two scales), then the browser
  // places it with object-fit, exactly as it does for an image asset
  const wanted = Math.max(innerWidth * ratio / base.width, innerHeight * ratio / base.height)
  const reference = REFERENCE_SIDE / Math.max(base.width, base.height)

  // A canvas that is too large for the graphics stack may be refused or may stay blank without any error: the picture is checked, and drawn again smaller
  // until something shows. A page that is blank on purpose is blank in the small drawing too, so it is left alone and nothing is reported.
  let scale = wanted
  let canvas = await draw(page, scale)
  if (!canvas || isBlank(canvas)) {
    const small = await draw(page, Math.min(reference, wanted))
    const really = !small || isBlank(small)
    if (!really) {
      canvas = null
      while (scale > reference) {
        scale = Math.max(scale / 2, reference)
        canvas = await draw(page, scale)
        if (canvas && !isBlank(canvas)) break
        canvas = null
      }
      report(`the page did not draw at ${Math.round(wanted * base.width)} px and was drawn smaller`, Boolean(canvas))
      canvas = canvas || small
    } else {
      canvas = small || canvas
    }
  }
  if (canvas) document.body.appendChild(canvas)
  document.title = `${pageNumber}/${pdf.numPages}`
}

async function draw(page, scale) {
  const viewport = page.getViewport({scale})
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.floor(viewport.width))
  canvas.height = Math.max(1, Math.floor(viewport.height))
  canvas.style.cssText = `display:block;width:100vw;height:100vh;object-fit:${fit}`
  const context = canvas.getContext('2d')
  if (!context) return null
  try {
    await page.render({canvasContext: context, viewport, background: 'rgb(255,255,255)'}).promise
  } catch (error) {
    return null
  }
  return canvas
}

// True when every sampled pixel is the same: a drawn page of any content has at least two different ones
function isBlank(canvas) {
  let context
  try { context = canvas.getContext('2d') } catch (error) { return true }
  let first = null
  for (let i = 0; i < SAMPLES; i++) {
    for (let j = 0; j < SAMPLES; j++) {
      let pixel
      try {
        pixel = context.getImageData(Math.floor((i + 0.5) * canvas.width / SAMPLES), Math.floor((j + 0.5) * canvas.height / SAMPLES), 1, 1).data
      } catch (error) {
        return true
      }
      const value = pixel[0] | pixel[1] << 8 | pixel[2] << 16 | pixel[3] << 24
      if (first === null) first = value
      else if (value !== first) return false
    }
  }
  return true
}

// Tells the manager (this page is served to the local viewer only), which raises a notification for the administrators
function report(problem, recovered) {
  fetch('/pdf-view/failed?' + new URLSearchParams({file, problem, recovered: recovered ? '1' : '0'}), {method: 'POST'}).catch(() => {})
}

render().catch(error => { console.error('Cannot show the PDF', error); report('the file could not be opened or drawn', false) })
