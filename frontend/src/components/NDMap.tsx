import { useEffect, useMemo, useState } from 'react'
import L from 'leaflet'
import { MapContainer, Marker, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

export type NDCityMarker = {
  city: string
  coordinates: [number, number]
  color: string
  value: string
  nd: number | null
}

function CityLayers({ points, selected, onSelect }: {
  points: NDCityMarker[]; selected: string; onSelect: (city: string) => void
}) {
  const map = useMap()
  const [viewport, setViewport] = useState(0)
  const refresh = () => setViewport(v => v + 1)
  useMapEvents({ moveend: refresh, resize: refresh })
  useEffect(() => {
    const fit = () => {
      const { clientWidth, clientHeight } = map.getContainer()
      if (!clientWidth || !clientHeight) return
      map.invalidateSize({ pan: false })
      map.fitBounds(L.latLngBounds(points.map(p => p.coordinates)), { padding: [36, 58], maxZoom: 8, animate: false })
      refresh()
    }
    const observer = new ResizeObserver(fit)
    fit()
    observer.observe(map.getContainer())
    return () => observer.disconnect()
  }, [map, points])
  useEffect(() => {
    const focus = points.find(p => p.city === selected)
    if (focus && !map.getBounds().pad(-0.1).contains(focus.coordinates)) map.panTo(focus.coordinates)
  }, [map, points, selected])

  const labels = useMemo(() => {
    const result = new Set<string>()
    const boxes: L.Bounds[] = []
    const size = map.getSize()
    const ordered = [...points].sort((a, b) =>
      Number(b.city === selected) - Number(a.city === selected) || (b.nd ?? -1) - (a.nd ?? -1))
    for (const point of ordered) {
      const { x, y } = map.latLngToContainerPoint(point.coordinates)
      const halfWidth = Math.min(124, (point.city.length + point.value.length + 3) * 3.7 + 10)
      const box = L.bounds([x - halfWidth, y - 41], [x + halfWidth, y - 11])
      if (point.city !== selected && (box.min!.x < 0 || box.max!.x > size.x || box.min!.y < 0 || y > size.y)) continue
      if (boxes.some(other => other.intersects(box))) continue
      result.add(point.city)
      boxes.push(box)
    }
    return result
  }, [map, points, selected, viewport])
  const icons = useMemo(() => points.map(p => {
    const dot = document.createElement('span')
    dot.className = `nd-dot${selected === p.city ? ' selected' : ''}`
    dot.style.backgroundColor = p.color
    return L.divIcon({ html: dot, className: 'nd-marker', iconSize: [16, 16], iconAnchor: [8, 8] })
  }), [points, selected])

  return <>{points.map((p, i) => <Marker key={p.city} position={p.coordinates} icon={icons[i]}
    title={`${p.city} · ${p.value}`} zIndexOffset={selected === p.city ? 1000 : 0}
    eventHandlers={{ click: () => onSelect(p.city) }}>
    {labels.has(p.city) && <Tooltip direction="top" permanent offset={[0, -10]} className="nd-map-label">
      <b>{p.city}</b> · {p.value}
    </Tooltip>}
  </Marker>)}</>
}

export function NDMap({ points, selected, onSelect }: {
  points: NDCityMarker[]; selected: string; onSelect: (city: string) => void
}) {
  return <div className="mapbox market-map nd-map" role="region" aria-label="Карта нумерической дистрибуции">
    <MapContainer center={points[0].coordinates} zoom={6} style={{ height: '100%', width: '100%' }}
      scrollWheelZoom={false} zoomSnap={0.5}>
      <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; участники <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' />
      <CityLayers points={points} selected={selected} onSelect={onSelect} />
    </MapContainer>
  </div>
}
