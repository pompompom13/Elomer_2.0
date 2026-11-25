import { useEffect } from 'react'
import L from 'leaflet'
import { MapContainer, TileLayer, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

/** Точка: [широта, долгота, …]. Цвет — по выбору вызывающего (функция от точки). */
export type Dot = number[]

/** Подпись точки по её номеру (запрос к серверу). */
export type DotInfo = (i: number) => Promise<{ title: string; lines: string[] } | null>

interface Props {
  dots: Dot[]
  color: (d: Dot) => string
  fitKey: string
  info?: DotInfo
  /** Во сколько раз точка больше обычной (0 — не рисовать). Нет — все точки одного размера. */
  size?: (d: Dot) => number
}

const MAX_SIZE = 5
// на мелком масштабе крупные точки сливаются в пятно — размер ограничен сильнее
const zoomCap = (z: number) => (z <= 4 ? 1.8 : z <= 5 ? 2.4 : z <= 7 ? 3.4 : MAX_SIZE)
const radius = (z: number) => (z <= 4 ? 1.3 : z <= 6 ? 1.8 : z <= 8 ? 2.4 : z <= 10 ? 3.2 : 4)

const TILE = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const ATTR = '&copy; участники <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
const CELL = 4 // ячейка индекса, px на нулевом масштабе (мир — 256 px)

/**
 * Слой из десятков тысяч точек: рисуется на плитках canvas, а не отдельными маркерами, поэтому
 * 85 тыс. аптек двигаются и масштабируются без задержек. Точки заранее разложены по сетке,
 * плитка берёт только свои ячейки.
 */
function DotsLayer({ dots, color, info, size }: { dots: Dot[]; color: (d: Dot) => string; info?: DotInfo; size?: (d: Dot) => number }) {
  const map = useMap()
  useEffect(() => {
    const crs = map.options.crs!
    const n = 256 / CELL
    const grid = new Map<number, { x: number; y: number; c: string; i: number; s: number }[]>()
    dots.forEach((d, i) => {
      const s = size ? Math.min(MAX_SIZE, size(d)) : 1
      if (s <= 0) return
      const p = crs.latLngToPoint(L.latLng(d[0], d[1]), 0)
      const k = Math.floor(p.y / CELL) * n + Math.floor(p.x / CELL)
      let b = grid.get(k)
      if (!b) { b = []; grid.set(k, b) }
      b.push({ x: p.x, y: p.y, c: color(d), i, s })
    })
    if (size) for (const b of grid.values()) b.sort((a, c) => c.s - a.s)   // крупные снизу, мелкие поверх
    const smax = size ? MAX_SIZE : 1
    const Layer = L.GridLayer.extend({
      createTile(coords: L.Coords) {
        const tile = document.createElement('canvas')
        const size = this.getTileSize()
        const ratio = window.devicePixelRatio || 1
        tile.width = size.x * ratio
        tile.height = size.y * ratio
        tile.style.width = `${size.x}px`
        tile.style.height = `${size.y}px`
        const ctx = tile.getContext('2d')!
        ctx.scale(ratio, ratio)
        const z = coords.z, scale = 2 ** z
        const r = radius(z), cap = zoomCap(z)
        const pad = r * Math.min(smax, cap) + 1
        // границы плитки в координатах нулевого масштаба
        const x0 = (coords.x * size.x - pad) / scale, x1 = ((coords.x + 1) * size.x + pad) / scale
        const y0 = (coords.y * size.y - pad) / scale, y1 = ((coords.y + 1) * size.y + pad) / scale
        const cx0 = Math.max(0, Math.floor(x0 / CELL)), cx1 = Math.min(n - 1, Math.floor(x1 / CELL))
        const cy0 = Math.max(0, Math.floor(y0 / CELL)), cy1 = Math.min(n - 1, Math.floor(y1 / CELL))
        ctx.globalAlpha = size ? (z <= 4 ? 0.7 : 0.55) : z <= 5 ? 0.55 : 0.8
        for (let cy = cy0; cy <= cy1; cy++) {
          for (let cx = cx0; cx <= cx1; cx++) {
            const b = grid.get(cy * n + cx)
            if (!b) continue
            for (const p of b) {
              if (p.x < x0 || p.x > x1 || p.y < y0 || p.y > y1) continue
              ctx.fillStyle = p.c
              ctx.beginPath()
              ctx.arc(p.x * scale - coords.x * size.x, p.y * scale - coords.y * size.y, r * Math.min(p.s, cap), 0, Math.PI * 2)
              ctx.fill()
            }
          }
        }
        return tile
      },
    })
    const layer = new (Layer as unknown as new (o: L.GridLayerOptions) => L.GridLayer)({ pane: 'overlayPane', updateWhenZooming: false, minZoom: 0, maxZoom: 19, zIndex: 5 })
    layer.addTo(map)
    // клик по точке — подпись учреждения
    const near = (e: L.LeafletMouseEvent) => {
      if (!info) return null
      const p = crs.latLngToPoint(e.latlng, 0), scale = 2 ** map.getZoom()
      const rz = radius(map.getZoom())
      const tol = Math.max(7, rz * smax) / scale   // не меньше 7 px на экране
      let best: { i: number; d: number } | null = null
      for (let cy = Math.floor((p.y - tol) / CELL); cy <= Math.floor((p.y + tol) / CELL); cy++) {
        for (let cx = Math.floor((p.x - tol) / CELL); cx <= Math.floor((p.x + tol) / CELL); cx++) {
          for (const q of grid.get(cy * n + cx) ?? []) {
            const d = Math.hypot(q.x - p.x, q.y - p.y)
            if (d <= Math.max(7, rz * Math.min(q.s, zoomCap(map.getZoom()))) / scale && (!best || d < best.d)) best = { i: q.i, d }
          }
        }
      }
      return best
    }
    const onClick = (e: L.LeafletMouseEvent) => {
      const best = near(e)
      if (!best || !info) return
      const d = dots[best.i]
      const pop = L.popup({ maxWidth: 300 }).setLatLng([d[0], d[1]]).setContent('<span class="muted">Загружаю…</span>').openOn(map)
      info(best.i).then((r) => {
        const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!))
        pop.setContent(r ? `<b>${esc(r.title)}</b>${r.lines.filter(Boolean).map((l) => `<br><span class="muted">${esc(l)}</span>`).join('')}` : 'Нет данных')
      }).catch(() => pop.setContent('Не удалось загрузить — обновите карту'))
    }
    const onMove = (e: L.LeafletMouseEvent) => { map.getContainer().style.cursor = near(e) ? 'pointer' : '' }
    map.on('click', onClick)
    map.on('mousemove', onMove)
    return () => { layer.remove(); map.off('click', onClick); map.off('mousemove', onMove); map.getContainer().style.cursor = '' }
  }, [map, dots, color, info, size])
  return null
}

// вид карты переживает переключение слоя (ЛПУ ↔ аптеки ↔ врачи): масштаб не сбрасывается
let lastView: { key: string; center: L.LatLng; zoom: number } | null = null

function Fit({ dots, fitKey }: { dots: Dot[]; fitKey: string }) {
  const map = useMap()
  useEffect(() => {
    const save = () => { lastView = { key: fitKey, center: map.getCenter(), zoom: map.getZoom() } }
    map.on('moveend', save)
    return () => { map.off('moveend', save) }
  }, [fitKey, map])
  useEffect(() => {
    if (lastView && lastView.key === fitKey) {   // та же территория — остаёмся, где были
      map.invalidateSize()
      map.setView(lastView.center, lastView.zoom, { animate: false })
      return
    }
    const fit = () => {
      map.invalidateSize()
      if (dots.length > 1) {
        // по 1–99 перцентилям, чтобы одинокая точка на краю страны не сжимала карту
        const la = dots.map((d) => d[0]).sort((a, b) => a - b), lo = dots.map((d) => d[1]).sort((a, b) => a - b)
        const q = (a: number[], p: number) => a[Math.min(a.length - 1, Math.max(0, Math.round((a.length - 1) * p)))]
        map.fitBounds(L.latLngBounds([q(la, 0.005), q(lo, 0.005)], [q(la, 0.995), q(lo, 0.995)]), { padding: [20, 20], maxZoom: 9 })
      }
    }
    fit()
    const t = setTimeout(fit, 120)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey, map])
  return null
}

/** Карта России с точками всех учреждений по фильтрам «Базы». */
export function DotsMap({ dots, color, fitKey, info, size }: Props) {
  return (
    <MapContainer center={[58, 60]} zoom={3} scrollWheelZoom={false} preferCanvas style={{ height: '100%', width: '100%' }}>
      <TileLayer url={TILE} attribution={ATTR} />
      <DotsLayer dots={dots} color={color} info={info} size={size} />
      <Fit dots={dots} fitKey={fitKey} />
    </MapContainer>
  )
}
