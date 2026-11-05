"""Two bounded product jobs: local preparation, serialized validated publication."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import csv
import hashlib
import json
import os
import re
import threading
from pathlib import Path
import shutil
import tempfile
import time
from PIL import Image

WORKERS=2
JOURNAL='批次进度追加.jsonl'
LOCK='批次运行锁'

class ProgressWriteError(OSError):
    pass

def signature(path):
    info=Path(path).stat()
    return (info.st_size,info.st_mtime_ns)

def sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()

def input_snapshot(folder,extensions,limit):
    if limit<0: raise ValueError('测试数量不能为负数')
    rows=[]
    # Windows directory entries already contain size/time metadata. Fetch it once.
    with os.scandir(folder) as entries:
        for entry in entries:
            if Path(entry.name).suffix.lower() in extensions and entry.is_file():
                stat=entry.stat()
                rows.append((Path(entry.path),stat.st_size,stat.st_mtime_ns))
    rows.sort(key=lambda row:[int(p) if p.isdigit() else p.casefold() for p in re.split(r'(\d+)',row[0].name)])
    return rows[:limit] if limit else rows

def prepare_product(renderer,source,local_dir,settings,expected_signature):
    """Workers only write their own local input/output cache, never the batch."""
    source=Path(source)
    if signature(source)!=tuple(expected_signature): raise ValueError('原图在批次准备后发生变化，请新建批次。')
    job=Path(tempfile.mkdtemp(prefix='sku_',dir=str(local_dir)))
    try:
        local_input=job/('input'+source.suffix)
        shutil.copyfile(source,local_input)
        files=[]
        for index,(_,image) in enumerate(renderer.render(local_input,settings),1):
            path=job/f'render_{index:02d}.{settings["format"]}'
            size=image.size
            try:
                def encode(target):
                    if settings['format']=='jpg':
                        image.save(target,format='JPEG',quality=settings['quality'],subsampling=0,dpi=(settings['ppi'],)*2)
                    else:image.save(target,format='PNG',dpi=(settings['ppi'],)*2)
                if renderer.data.get('workbench') and renderer.data['scenes'][index-1].get('fixed'):
                    # One encoded fixed page per batch, then copy it into each SKU.
                    fixed=Path(local_dir)/f'fixed_{index:02d}.{settings["format"]}'
                    with renderer._fixed_export_lock:
                        if not fixed.exists():encode(fixed)
                    shutil.copyfile(fixed,path)
                else:encode(path)
                with Image.open(path) as check:
                    check.load()
                    if check.size!=size: raise ValueError('本地成品尺寸校验失败')
                    if settings['format']=='png' and 'A' in image.getbands() and 'A' not in check.getbands():
                        raise ValueError('本地成品透明通道校验失败')
                files.append({'index':index,'path':path,'bytes':path.stat().st_size,'sha256':sha256(path)})
            finally: image.close()
        if len(files)!=len(renderer.data['scenes']): raise ValueError('生成的场景张数不完整')
        if signature(source)!=tuple(expected_signature): raise ValueError('处理期间原图发生变化，请新建批次。')
        local_input.unlink()
        return {'dir':job,'files':files,'format':settings['format']}
    except BaseException:
        remove_local_job(job,Path(local_dir))
        raise

def remove_local_job(path,root):
    path=Path(path)
    if not path.exists(): return
    if path.resolve().parent!=Path(root).resolve() or not path.name.startswith('sku_'):
        raise ValueError('本地临时目录范围无效')
    shutil.rmtree(path)

def remove_upload(path,batch):
    path=Path(path)
    if not path.exists(): return
    if path.resolve().parent!=Path(batch).resolve() or not path.name.startswith('.upload_'):
        raise ValueError('上传临时目录范围无效')
    shutil.rmtree(path)

def append_record(journal,name,record):
    try:
        journal.write(json.dumps({'name':name,'record':record},ensure_ascii=False,separators=(',',':'))+'\n')
        journal.flush();os.fsync(journal.fileno())
    except OSError as error:
        raise ProgressWriteError('无法持久保存批次进度，已停止发布新结果。') from error

def copy_validated(file,target):
    digest=hashlib.sha256();size=0
    with open(file['path'],'rb') as source,open(target,'xb') as out:
        for block in iter(lambda:source.read(1024*1024),b''):
            out.write(block);digest.update(block);size+=len(block)
        out.flush()
    if size!=file['bytes'] or digest.hexdigest()!=file['sha256'] or target.stat().st_size!=size:
        raise ValueError('缓存或写入校验失败，未发布这一套结果。')

def publish_product(prepared,dest,source_name,record_before,journal,state,safe_name,naming=None):
    """Upload a complete local set; atomically expose a new SKU directory."""
    stem=naming['folder'] if naming else safe_name(Path(source_name).stem);product=dest/stem
    names=naming['files'] if naming else [f'{stem}_{file["index"]:02d}.{prepared["format"]}' for file in prepared['files']]
    if product.exists():
        if not product.is_dir() or product.is_symlink() or (hasattr(product,'is_junction') and product.is_junction()):
            raise ValueError('已有产品输出路径类型无效，未覆盖。')
        if product.resolve().parent!=dest.resolve(): raise ValueError('已有产品输出路径超出批次目录。')
        allowed=set(names)|{name+'.part' for name in names}
        if any(not path.is_file() or path.name not in allowed for path in product.iterdir()):
            raise ValueError('产品目录含非本批次预期的文件，未覆盖，请检查后继续。')
    stage=Path(tempfile.mkdtemp(prefix='.upload_',dir=str(dest)))
    try:
        for file,name in zip(prepared['files'],names): copy_validated(file,stage/name)
        outputs=[str(Path(stem)/name) for name in names]
        record={'status':'成功','count':len(names),'outputs':outputs,
                'sizes':{rel:file['bytes'] for rel,file in zip(outputs,prepared['files'])},
                'sha256':{rel:file['sha256'] for rel,file in zip(outputs,prepared['files'])}}
        # The expected final content is durable before directory/file publication.
        # After a crash, a complete published set can be recognized on resume.
        append_record(journal,source_name,record)
        if not product.exists():
            os.rename(stage,product)
        else:
            # Regenerate only the expected files of an incomplete prior SKU.
            for name in names: os.replace(stage/name,product/name)
            stage.rmdir()
        state['records'][source_name]=record
        return record
    finally:
        remove_upload(stage,dest)

@contextmanager
def batch_lock(dest):
    with open(dest/LOCK,'a+b') as stream:
        stream.seek(0,os.SEEK_END)
        if not stream.tell(): stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('这个批次正在被另一个窗口处理，请先停止另一个窗口。') from error
        try: yield
        finally:
            stream.seek(0)
            if os.name=='nt': msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

def replay_journal(dest,state):
    path=dest/JOURNAL
    if not path.exists(): return
    with open(path,'r+b') as stream:
        while True:
            offset=stream.tell();line=stream.readline()
            if not line: break
            if not line.endswith(b'\n'):
                stream.seek(offset);stream.truncate();stream.flush();os.fsync(stream.fileno());break
            try:
                entry=json.loads(line)
                if not isinstance(entry['name'],str) or not isinstance(entry['record'],dict): raise ValueError()
                state['records'][entry['name']]=entry['record']
            except (ValueError,KeyError,TypeError) as error:
                raise ValueError('批次进度追加文件损坏，请保留文件并检查。') from error

def completed_record(dest,record,expected):
    if record.get('status')!='成功' or len(record.get('outputs',[]))!=expected: return False
    for relative in record['outputs']:
        path=dest/relative
        if Path(relative).is_absolute() or dest.resolve() not in path.resolve().parents: return False
        try:
            stat=path.stat()
            if not path.is_file() or (relative in record.get('sizes',{}) and stat.st_size!=record['sizes'][relative]): return False
            expected_hash=record.get('sha256',{}).get(relative)
            if expected_hash and sha256(path)!=expected_hash:return False
        except OSError: return False
    return True

def write_csv(path,files,records):
    temp=path.with_name(path.name+'.part')
    with open(temp,'w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['输入文件','结果','输出张数','说明'])
        for source,_,_ in files:
            row=records.get(source.name)
            if row: writer.writerow([source.name,row['status'],row.get('count',0),row.get('error','')])
        stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)

def checkpoint(api,dest,state,files,journal=None):
    path=dest/'运行状态.json';temporary=path.with_name(path.name+'.part')
    with open(temporary,'w',encoding='utf-8') as stream:
        json.dump(state,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    write_csv(dest/'处理记录.csv',files,state['records'])
    if journal is not None:
        journal.seek(0);journal.truncate();journal.flush();os.fsync(journal.fileno())

def run_batch(api,manifest,input_folder,output_folder,settings,limit=0,resume=None,control=None,progress=lambda *args:None,workers=WORKERS):
    start=time.perf_counter()
    if isinstance(workers,bool) or not isinstance(workers,int) or workers not in (1,2): raise ValueError('并行套数只能为1或2。')
    settings=api.settings_checked(settings);control=control or api.Control()
    source=Path(input_folder).resolve()
    files=input_snapshot(source,api.EXTENSIONS,limit)
    if not files: raise ValueError('输入文件夹中没有支持的图片；不读取子文件夹')
    from export_naming import plan_outputs
    plans=plan_outputs(files,len(json.loads(Path(manifest).read_text(encoding='utf-8'))['scenes']),settings,api.safe_name)
    identity={'template_hash':hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),'input':str(source),
              'settings':settings,'naming_schema':'custom_patterns_v2' if 'naming' in settings else 'source_sequence_v1',
              'files':[[path.name,size,mtime] for path,size,mtime in files]}
    dest=Path(resume) if resume else api.unique_dir(output_folder,'高速套图')
    with batch_lock(dest):
        if resume:
            state=json.loads((dest/'运行状态.json').read_text(encoding='utf-8'))
            if state['identity'].get('naming_schema') not in ('source_sequence_v1','custom_patterns_v2'): raise ValueError('这个旧批次的命名规则不同，请新建批次。')
            if state['identity']!=identity: raise ValueError('输入文件、模板或输出设置与原批次不同，不能继续该批次')
            replay_journal(dest,state)
        else: state={'identity':identity,'records':{},'template_path':str(Path(manifest).resolve())}
        renderer=api.Renderer(manifest)  # Fully initialized shared, read-only assets/maps.
        renderer._fixed_export_lock=threading.Lock()
        expected=len(renderer.data['scenes']);state['status']='运行中'
        state['batch_mode']='two_product_local_pipeline_v1'
        checkpoint(api,dest,state,files)
        progress(0,len(files),'','运行中',str(dest))
        completed=0;next_index=0;pending={};failed=False
        with open(dest/JOURNAL,'a+',encoding='utf-8',newline='\n') as journal, \
             open(dest/'处理记录.csv','a',encoding='utf-8-sig',newline='') as csv_stream:
            writer=csv.writer(csv_stream)
            try:
                with tempfile.TemporaryDirectory(prefix='carousel_batch_') as local,ThreadPoolExecutor(max_workers=workers,thread_name_prefix='carousel') as pool:
                    local_root=Path(local).resolve()
                    if local_root.parent!=Path(tempfile.gettempdir()).resolve() or not local_root.name.startswith('carousel_batch_'):
                        raise ValueError('本地临时目录范围无效')
                    while next_index<len(files) or pending:
                        while next_index<len(files) and len(pending)<workers and not control.stop.is_set() and not control.paused.is_set():
                            path,size,mtime=files[next_index];next_index+=1
                            old=state['records'].get(path.name,{})
                            if completed_record(dest,old,expected):
                                completed+=1;progress(completed,len(files),path.name,'已完成，跳过',str(dest));continue
                            job=pool.submit(prepare_product,renderer,path,local_root,settings,(size,mtime))
                            pending[job]=(path,old)
                        if not pending:
                            if control.stop.is_set(): break
                            control.stop.wait(.1);continue
                        ready,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                        # Refill the freed slot after each publication so the next
                        # local product can render while this coordinator uploads.
                        for job in list(ready)[:1]:
                            path,old=pending.pop(job);prepared=None
                            try:
                                prepared=job.result()
                                record=publish_product(prepared,dest,path.name,old,journal,state,api.safe_name,plans[path.name])
                            except ProgressWriteError: raise
                            except Exception as error:
                                record={'status':'失败','count':0,'outputs':[],'error':str(error)}
                                append_record(journal,path.name,record);state['records'][path.name]=record
                            finally:
                                if prepared is not None: remove_local_job(prepared['dir'],local_root)
                            writer.writerow([path.name,record['status'],record.get('count',0),record.get('error','')]);csv_stream.flush()
                            completed+=1;progress(completed,len(files),path.name,record['status'],str(dest))
            except BaseException:
                failed=True;raise
            finally:
                state['status']='处理中断' if failed else ('已停止' if control.stop.is_set() else '已完成')
                state['last_run_seconds']=round(time.perf_counter()-start,3)
                csv_stream.flush();csv_stream.close()
                checkpoint(api,dest,state,files,journal)
        return dest,state
