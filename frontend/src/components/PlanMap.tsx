import { useEffect } from 'react'
import L from 'leaflet'
import { CircleMarker, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

export interface MapPoint {
  id: string
  lat: number
  lon: number
  kind: 'lpu' | 'pharmacy'
  label?: string // номер в маршруте
  title: string
  color?: string
  dim?: boolean
  r?: number // радиус кружка, px
}

interface Props {
  center: [number, number]
  points: MapPoint[]
  route?: [number, number][]
  home?: [number, number] | null
  routeColor?: string
  highlight?: string | null
  onHover?: (id: string | null) => void
  onPick?: (latlng: [number, number]) => void
  picking?: boolean
  fitKey: string
  onSelect?: (id: string) => void
  maxZoom?: number
  wheelZoom?: boolean
}

const TILE = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const ATTR = '&copy; участники <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'

function pinIcon(p: MapPoint, hl: boolean) {
  const bg = p.color ?? (p.kind === 'lpu' ? 'var(--doc)' : 'var(--ph)')
  const size = hl ? 28 : 22
  return L.divIcon({
    className: '',
    html: `<div class="pin${p.kind === 'pharmacy' ? ' ph' : ''}" style="background:${bg};width:${size}px;height:${size}px;line-height:${size}px">${p.label ?? ''}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

const homeIcon = L.divIcon({ className: '', html: '<div class="pin home"></div>', iconSize: [14, 14], iconAnchor: [7, 7] })

function Fit({ pts, fitKey, maxZoom = 15 }: { pts: [number, number][]; fitKey: string; maxZoom?: number }) {
  const map = useMap()
  useEffect(() => {
    const fit = () => {
      map.invalidateSize()
      if (pts.length > 1) map.fitBounds(L.latLngBounds(pts), { padding: [30, 30], maxZoom })
      else if (pts.length === 1) map.setView(pts[0], 14)
    }
    fit()
    // контейнер карты может получить размер позже (сетка, прокрутка) — пересчитываем
    const t = setTimeout(fit, 120)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey])
  useEffect(() => {
    const el = map.getContainer()
    let t: ReturnType<typeof setTimeout> | undefined
    const ro = new ResizeObserver(() => {
      map.invalidateSize()
      // обзорная карта (страна) после смены размера окна снова вписывается целиком
      if (maxZoom <= 8 && pts.length > 1) { clearTimeout(t); t = setTimeout(() => map.fitBounds(L.latLngBounds(pts), { padding: [20, 20], maxZoom }), 150) }
    })
    ro.observe(el)
    return () => { ro.disconnect(); clearTimeout(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, maxZoom, pts.length])
  return null
}

function Picker({ onPick, active }: { onPick?: (l: [number, number]) => void; active?: boolean }) {
  const map = useMapEvents({
    click(e) {
      if (active && onPick) onPick([e.latlng.lat, e.latlng.lng])
    },
  })
  useEffect(() => {
    map.getContainer().style.cursor = active ? 'crosshair' : ''
  }, [active, map])
  return null
}

export function PlanMap(props: Props) {
  const { center, points, route, home, routeColor, highlight, onHover, onPick, picking, fitKey, onSelect, maxZoom } = props
  const numbered = points.filter((p) => p.label)
  const plain = points.filter((p) => !p.label)
  const fitPts: [number, number][] = route?.length ? route : points.map((p) => [p.lat, p.lon])
  return (
    <MapContainer center={center} zoom={12} style={{ height: '100%', width: '100%' }} scrollWheelZoom={props.wheelZoom ?? true} preferCanvas>
      <TileLayer url={TILE} attribution={ATTR} />
      <Fit pts={fitPts} fitKey={fitKey} maxZoom={maxZoom} />
      <Picker onPick={onPick} active={picking} />
      {plain.map((p) => (
        <CircleMarker key={p.id} center={[p.lat, p.lon]} radius={p.r ?? (p.kind === 'lpu' ? 5 : 3.5)}
          eventHandlers={onSelect ? { click: () => onSelect(p.id) } : undefined}
          pathOptions={{
            color: '#fff', weight: 1, fillOpacity: p.dim ? 0.35 : p.r && p.r > 8 ? 0.7 : 0.9,
            fillColor: p.color ?? (p.kind === 'lpu' ? '#2A52AD' : '#4596E0'),
          }}>
          <Tooltip>{p.title}</Tooltip>
        </CircleMarker>
      ))}
      {route && route.length > 1 && (
        <Polyline positions={route} pathOptions={{ color: routeColor ?? '#16233A', weight: 3, opacity: 0.75 }} />
      )}
      {numbered.map((p) => (
        <Marker key={p.id} position={[p.lat, p.lon]} icon={pinIcon(p, highlight === p.id)}
          zIndexOffset={highlight === p.id ? 1000 : 0}
          eventHandlers={{ mouseover: () => onHover?.(p.id), mouseout: () => onHover?.(null) }}>
          <Tooltip direction="top" offset={[0, -12]}>{p.title}</Tooltip>
        </Marker>
      ))}
      {home && (
        <Marker position={home} icon={homeIcon}>
          <Tooltip>Точка старта</Tooltip>
        </Marker>
      )}
    </MapContainer>
  )
}
