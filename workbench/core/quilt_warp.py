"""Photoshop quilt-warp rasterization using NumPy and Pillow only.

Geometry is approximated by bicubic-patch triangles. Control-point bounds,
not nominal source bounds, are mapped to the placed layer's non-affine quad.
Maps are normalized source coordinates, independent of input PPI or pixels.
Overlapping/folded patches use source-order painter precedence; such meshes
need a Photoshop comparison before production use.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
from PIL import Image

ALGORITHM_VERSION = 2


@dataclass
class QuiltSpec:
    controls: np.ndarray
    source_u: np.ndarray
    source_v: np.ndarray
    quad: np.ndarray
    native_size: tuple
    canvas_size: tuple
    source_origin: tuple

    @property
    def patch_shape(self):
        return (self.controls.shape[0] - 1) // 3, (self.controls.shape[1] - 1) // 3

    def identity(self):
        payload = {'algorithm':ALGORITHM_VERSION, 'controls':self.controls.tolist(),
                   'u':self.source_u.tolist(),'v':self.source_v.tolist(),
                   'quad':self.quad.tolist(),'native':self.native_size,'canvas':self.canvas_size}
        return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def _number(value):
    return float(getattr(value, 'value', value))


def _values(value):
    return np.asarray(value.values, dtype=np.float64)


def _slices(value, axis, native_length, expected):
    """Interpret Photoshop's outer +/-0.6-pixel slice sentinels.

    Interiors mark original source-space splits. Some files retain a translated
    source coordinate frame; its centered native rectangle gives the origin.
    """
    slices = _values(value[axis])
    if len(slices) != expected + 1 or not np.isfinite(slices).all() or not np.all(np.diff(slices) > 0):
        raise ValueError('Invalid quilt slice sequence')
    span = slices[-1] - slices[0]
    if abs(span - native_length - 1.2) > .03:
        raise ValueError('Unsupported quilt slice extent; expected native bounds plus 0.6-pixel sentinels')
    origin = (slices[0] + slices[-1] - native_length) / 2
    breaks = (slices - origin) / native_length
    breaks[0], breaks[-1] = 0., 1.
    if not np.all(np.diff(breaks) > 0) or not np.all((breaks >= 0) & (breaks <= 1)):
        raise ValueError('Quilt slices fall outside the original source rectangle')
    return breaks, float(origin)


def parse_quilt(descriptor, canvas_size):
    """Parse the SoLd/SoLE descriptor of one smart object into a validated spec."""
    q = descriptor.get(b'quiltWarp')
    if q is None:
        raise ValueError('No quiltWarp descriptor')
    if getattr(q.get(b'warpStyle'), 'enum', None) != b'warpCustom':
        raise ValueError('Only custom quilt-warp meshes are supported')
    if int(_number(q.get(b'uOrder', 0))) != 4 or int(_number(q.get(b'vOrder', 0))) != 4:
        raise ValueError('Only bicubic quilt patches are supported')
    for key in (b'warpValue', b'warpPerspective', b'warpPerspectiveOther'):
        if abs(_number(q.get(key,0))) > 1e-7:
            raise ValueError('Additional quilt warp/perspective parameters are not supported')
    rows, cols = int(_number(q[b'deformNumRows'])), int(_number(q[b'deformNumCols']))
    if min(rows,cols) < 4 or (rows-1)%3 or (cols-1)%3 or max(rows,cols)>100:
        raise ValueError('Invalid quilt control-grid shape')
    envelope=q[b'customEnvelopeWarp'];points=envelope[b'meshPoints']
    xs,ys=_values(points[b'Hrzn']),_values(points[b'Vrtc'])
    if len(xs)!=rows*cols or len(ys)!=rows*cols:
        raise ValueError('Quilt point count does not match grid shape')
    controls=np.stack((xs,ys),axis=-1).reshape(rows,cols,2)
    if not np.isfinite(controls).all():raise ValueError('Quilt mesh contains nonfinite coordinates')
    native=tuple(int(round(_number(descriptor[b'Sz  '][key]))) for key in (b'Wdth',b'Hght'))
    canvas=tuple(int(x) for x in canvas_size)
    if min(native+canvas)<1 or max(native+canvas)>16384 or canvas[0]*canvas[1]>40_000_000:
        raise ValueError('Quilt source or document size exceeds limits')
    source_u,ox=_slices(envelope[b'quiltSliceX'],b'quiltSliceX',native[0],(cols-1)//3)
    source_v,oy=_slices(envelope[b'quiltSliceY'],b'quiltSliceY',native[1],(rows-1)//3)
    quad_values=descriptor.get(b'nonAffineTransform',descriptor.get(b'Trnf'))
    if quad_values is None or len(quad_values)!=8:raise ValueError('Missing placed-layer quad')
    quad=np.array([_number(v) for v in quad_values]).reshape(4,2)
    if not np.isfinite(quad).all():raise ValueError('Invalid placed-layer quad')
    if np.any(np.ptp(controls.reshape(-1,2),axis=0)<1e-7):raise ValueError('Degenerate quilt mesh')
    _homography(quad)
    return QuiltSpec(controls,source_u,source_v,quad,native,canvas,(ox,oy))


def _homography(quad):
    edges=np.roll(quad,-1,axis=0)-quad
    turns=edges[:,0]*np.roll(edges[:,1],-1)-edges[:,1]*np.roll(edges[:,0],-1)
    if not (np.all(turns>1e-8) or np.all(turns<-1e-8)):
        raise ValueError('Placed-layer quad is not convex')
    matrix=[];right=[]
    for (x,y),(u,v) in zip(((0,0),(1,0),(1,1),(0,1)),quad):
        matrix.extend(((x,y,1,0,0,0,-u*x,-u*y),(0,0,0,x,y,1,-v*x,-v*y)))
        right.extend((u,v))
    try:result=np.r_[np.linalg.solve(matrix,right),1].reshape(3,3)
    except np.linalg.LinAlgError as exc:raise ValueError('Degenerate placed-layer quad') from exc
    return result


def _project(points,low,span,h):
    normalized=(points-low)/span
    p=np.concatenate((normalized,np.ones(normalized.shape[:-1]+(1,))),axis=-1)@h.T
    if np.any(np.abs(p[...,2])<1e-8):raise ValueError('Quilt perspective crosses infinity')
    return p[...,:2]/p[...,2,None]


def _basis(t):
    t=np.asarray(t)
    return np.stack(((1-t)**3,3*t*(1-t)**2,3*t*t*(1-t),t**3),axis=-1)


def _top_left(a,b):
    dx,dy=b-a
    return bool(dy < 0 or (abs(dy)<1e-10 and dx>0))


def _raster_triangle(positions,uv,umaps,vmaps,owners,offsets):
    width,height=umaps.shape[2],umaps.shape[1]
    a,b,c=positions
    determinant=np.cross(b-a,c-a).item()
    if abs(determinant)<1e-10:return False
    folded=determinant<0
    if folded:
        positions=positions[[0,2,1]];uv=uv[[0,2,1]];a,b,c=positions;determinant=-determinant
    x0=max(0,int(math.floor(float(positions[:,0].min())-1)))
    x1=min(width,int(math.ceil(float(positions[:,0].max())+1)))
    y0=max(0,int(math.floor(float(positions[:,1].min())-1)))
    y1=min(height,int(math.ceil(float(positions[:,1].max())+1)))
    if x1<=x0 or y1<=y0:return folded
    xx=np.arange(x0,x1,dtype=np.float64)[None,:]
    yy=np.arange(y0,y1,dtype=np.float64)[:,None]
    flags=(_top_left(b,c),_top_left(c,a),_top_left(a,b))
    for sample,(dx,dy) in enumerate(offsets):
        x,y=xx+dx,yy+dy
        w0=((b[0]-x)*(c[1]-y)-(b[1]-y)*(c[0]-x))/determinant
        w1=((c[0]-x)*(a[1]-y)-(c[1]-y)*(a[0]-x))/determinant
        w2=1-w0-w1
        inside=np.ones(w0.shape,dtype=bool)
        for weight,include in zip((w0,w1,w2),flags):
            inside &= weight>=-1e-10 if include else weight>1e-10
        if not inside.any():continue
        u=w0*uv[0,0]+w1*uv[1,0]+w2*uv[2,0]
        v=w0*uv[0,1]+w1*uv[1,1]+w2*uv[2,1]
        umaps[sample,y0:y1,x0:x1][inside]=u[inside]
        vmaps[sample,y0:y1,x0:x1][inside]=v[inside]
        owners[sample,y0:y1,x0:x1][inside]=True
    return folded


def build_uv_map(spec,cache_path=None,tolerance=.15,samples=2,max_subdiv=64,progress=None,project_controls=True):
    """Build reusable uint16 U/V and uint8 edge coverage maps.

    samples=2 uses a 2x2 subpixel grid, averaged to one source UV per pixel.
    The normal path needs roughly 200 MB temporary RAM on a 2000x2000 canvas.
    """
    if samples not in (1,2):raise ValueError('samples must be 1 or 2')
    if not .03<=tolerance<=2:raise ValueError('Invalid geometric tolerance')
    start=time.perf_counter();width,height=spec.canvas_size
    total=samples*samples
    offsets=[((x+.5)/samples,(y+.5)/samples) for y in range(samples) for x in range(samples)]
    umaps=np.zeros((total,height,width),dtype=np.float32)
    vmaps=np.zeros_like(umaps)
    owners=np.zeros((total,height,width),dtype=bool)
    low=spec.controls.reshape(-1,2).min(0);span=np.ptp(spec.controls.reshape(-1,2),axis=0)
    homography=_homography(spec.quad);triangles=folded=0;patches=[]
    rows,cols=spec.patch_shape
    for row in range(rows):
        for col in range(cols):
            control=spec.controls[row*3:row*3+4,col*3:col*3+4]
            projected_control=_project(control,low,span,homography)
            curvature=max(float(np.linalg.norm(np.diff(projected_control,n=2,axis=axis),axis=-1).max()) for axis in (0,1))
            steps=max(5,min(max_subdiv,int(math.ceil(math.sqrt(max(0,curvature)*3/(4*tolerance))))))
            t=np.linspace(0,1,steps+1);basis=_basis(t)
            if project_controls:
                geometry=np.einsum('ri,ijc,sj->rsc',basis,projected_control,basis)
            else:
                geometry=np.einsum('ri,ijc,sj->rsc',basis,control,basis)
                geometry=_project(geometry,low,span,homography)
            uu=spec.source_u[col]+t*(spec.source_u[col+1]-spec.source_u[col])
            vv=spec.source_v[row]+t*(spec.source_v[row+1]-spec.source_v[row])
            uv=np.stack(np.meshgrid(uu,vv),axis=-1)
            for r in range(steps):
                for c in range(steps):
                    for indices in (((r,c),(r,c+1),(r+1,c+1)),((r,c),(r+1,c+1),(r+1,c))):
                        p=np.array([geometry[index] for index in indices])
                        tex=np.array([uv[index] for index in indices])
                        folded += _raster_triangle(p,tex,umaps,vmaps,owners,offsets)
                        triangles += 1
            patches.append(steps)
            if progress:progress(row*cols+col+1,rows*cols)
    counts=owners.sum(0,dtype=np.uint8)
    divisor=np.maximum(counts,1)
    u=umaps.sum(0)/divisor;v=vmaps.sum(0)/divisor
    coverage=np.rint(counts.astype(np.float32)/total*255).astype(np.uint8)
    mapping={'u':np.rint(np.clip(u,0,1)*65535).astype(np.uint16),
             'v':np.rint(np.clip(v,0,1)*65535).astype(np.uint16),
             'coverage':coverage,
             'metadata':{'algorithm':ALGORITHM_VERSION,'identity':spec.identity(),
                         'canvas':list(spec.canvas_size),'native':list(spec.native_size),
                         'patches':list(spec.patch_shape),'subdivisions':patches,
                         'triangles':triangles,'folded_triangles':int(folded),'samples':samples,
                         'seconds':round(time.perf_counter()-start,3),
                         'coverage_bbox':_coverage_bbox(coverage),
                         'project_controls_first':bool(project_controls),
                         'note':'Bicubic triangle approximation; folded overlaps use source-order precedence.'}}
    if cache_path:save_map(cache_path,mapping)
    return mapping


def _coverage_bbox(coverage):
    rows=np.flatnonzero(np.any(coverage>0,axis=1));cols=np.flatnonzero(np.any(coverage>0,axis=0))
    if not len(rows) or not len(cols):return [0,0,0,0]
    return [int(cols[0]),int(rows[0]),int(cols[-1])+1,int(rows[-1])+1]


def use_cached_outline(mapping,cached_mask,max_gap=4):
    """Use a Photoshop-cached opaque source silhouette, without changing interior UV.

    Missing UV just outside the approximate triangulated outline is extended
    from immediate mapped neighbours; larger disagreement is rejected. This
    is valid only when the original smart-object content is fully opaque.
    """
    target=np.asarray(cached_mask.convert('L') if isinstance(cached_mask,Image.Image) else cached_mask,dtype=np.uint8)
    if target.shape!=mapping['coverage'].shape:raise ValueError('Cached outline has a different canvas size')
    result={'u':mapping['u'].copy(),'v':mapping['v'].copy(),'coverage':target.copy(),'metadata':dict(mapping['metadata'])}
    known=mapping['coverage']>0
    holes=(target>0)&~known
    fill_count=int(holes.sum())
    for _ in range(max_gap):
        if not holes.any():break
        count=np.zeros(target.shape,np.uint8);us=np.zeros(target.shape,np.float32);vs=np.zeros_like(us)
        for dy,dx in ((-1,0),(1,0),(0,-1),(0,1)):
            src_y=slice(max(0,-dy),target.shape[0]-max(0,dy));dst_y=slice(max(0,dy),target.shape[0]-max(0,-dy))
            src_x=slice(max(0,-dx),target.shape[1]-max(0,dx));dst_x=slice(max(0,dx),target.shape[1]-max(0,-dx))
            valid=known[src_y,src_x]
            count[dst_y,dst_x]+=valid
            us[dst_y,dst_x]+=result['u'][src_y,src_x]*valid
            vs[dst_y,dst_x]+=result['v'][src_y,src_x]*valid
        selected=holes&(count>0)
        result['u'][selected]=np.rint(us[selected]/count[selected]).astype(np.uint16)
        result['v'][selected]=np.rint(vs[selected]/count[selected]).astype(np.uint16)
        known[selected]=True;holes[selected]=False
    if holes.any():raise ValueError('Photoshop outline differs too far from the quilt geometry')
    result['metadata']['cached_outline_sha256']=hashlib.sha256(target.tobytes()).hexdigest()
    result['metadata']['outline_extended_pixels']=fill_count
    result['metadata']['coverage_bbox']=_coverage_bbox(target)
    return result


def save_map(path,mapping):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.part')
    with temporary.open('wb') as f:
        np.savez_compressed(f,u=mapping['u'],v=mapping['v'],coverage=mapping['coverage'],
                            metadata=np.array(json.dumps(mapping['metadata'])))
    temporary.replace(path)


def load_map(path,spec=None):
    with np.load(path,allow_pickle=False) as data:
        result={key:data[key] for key in ('u','v','coverage')}
        result['metadata']=json.loads(str(data['metadata']))
    if result['metadata']['algorithm']!=ALGORITHM_VERSION:raise ValueError('Quilt map algorithm mismatch')
    if not result['metadata'].get('project_controls_first'):
        raise ValueError('Experimental quilt projection cannot be used as a production cache')
    if spec and result['metadata']['identity']!=spec.identity():raise ValueError('Quilt geometry changed; rebuild mapping')
    if result['u'].shape!=result['v'].shape or result['u'].shape!=result['coverage'].shape:
        raise ValueError('Quilt map dimensions do not match')
    if result['u'].dtype!=np.uint16 or result['v'].dtype!=np.uint16 or result['coverage'].dtype!=np.uint8:
        raise ValueError('Quilt map data types are invalid')
    if list(result['u'].shape[::-1])!=result['metadata']['canvas']:
        raise ValueError('Quilt map canvas metadata does not match its arrays')
    return result


def render_rgba(artwork,mapping,chunk_rows=128):
    """Sample a pre-adapted artwork using premultiplied-alpha bilinear sampling."""
    src=np.asarray(artwork.convert('RGBA'),dtype=np.float32)/255
    sh,sw=src.shape[:2]
    opaque=bool(np.all(src[:,:,3]==1))
    if opaque:src=src[:,:,:3]
    else:src[:,:,:3]*=src[:,:,3:4]
    height,width=mapping['u'].shape
    result=np.zeros((height,width,4),dtype=np.uint8)
    left,first,right,last=mapping['metadata'].get('coverage_bbox',_coverage_bbox(mapping['coverage']))
    for top in range(first,last,chunk_rows):
        bottom=min(last,top+chunk_rows)
        x=mapping['u'][top:bottom,left:right].astype(np.float32)*np.float32(sw/65535)-np.float32(.5)
        y=mapping['v'][top:bottom,left:right].astype(np.float32)*np.float32(sh/65535)-np.float32(.5)
        np.clip(x,0,sw-1,out=x);np.clip(y,0,sh-1,out=y)
        x0=x.astype(np.int32);y0=y.astype(np.int32)
        x1=np.minimum(x0+1,sw-1);y1=np.minimum(y0+1,sh-1)
        wx=(x-x0.astype(np.float32))[:,:,None];wy=(y-y0.astype(np.float32))[:,:,None]
        sampled=(src[y0,x0]*(1-wx)+src[y0,x1]*wx)*(1-wy)+(src[y1,x0]*(1-wx)+src[y1,x1]*wx)*wy
        coverage=mapping['coverage'][top:bottom,left:right].astype(np.float32)[:,:,None]/255
        if opaque:rgba=np.concatenate((sampled,coverage),axis=2)
        else:
            alpha=sampled[:,:,3:4]
            rgb=np.divide(sampled[:,:,:3],alpha,out=np.zeros_like(sampled[:,:,:3]),where=alpha>1e-8)
            rgba=np.concatenate((rgb,alpha*coverage),axis=2)
        result[top:bottom,left:right]=np.rint(np.clip(rgba,0,1)*255).astype(np.uint8)
    return Image.fromarray(result,'RGBA')
