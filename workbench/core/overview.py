"""Bounded contact sheets made from rendered previews, never from original artwork."""
import math
from pathlib import Path
from PIL import Image,ImageOps,ImageDraw,ImageFont

def compose_sheet(items,destination,title):
    if not 1<=len(items)<=100:raise ValueError('一次总览支持 1–100 张图片，请分批选择')
    cols=min(4,max(1,math.ceil(math.sqrt(len(items)))))
    cell=320;label=48;gap=16;header=64
    rows=math.ceil(len(items)/cols)
    sheet=Image.new('RGB',(cols*(cell+gap)+gap,header+rows*(cell+label+gap)+gap),'#f3f6fb')
    try:
        try:font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',17)
        except OSError:font=ImageFont.load_default()
        draw=ImageDraw.Draw(sheet);draw.text((gap,20),title,fill='#182b49',font=font)
        for index,(path,name) in enumerate(items):
            x=gap+(index%cols)*(cell+gap);y=header+(index//cols)*(cell+label+gap)
            draw.rectangle((x,y,x+cell,y+cell+label),fill='white')
            with Image.open(path) as original:
                image=original.convert('RGBA');image.thumbnail((cell,cell),Image.Resampling.LANCZOS)
                sheet.paste(image,(x+(cell-image.width)//2,y+(cell-image.height)//2),image);image.close()
            text=str(name)
            while draw.textlength(text,font=font)>cell-16 and len(text)>2:text=text[:-2]+'…'
            draw.text((x+8,y+cell+12),text,fill='#182b49',font=font)
        sheet.save(destination,'JPEG',quality=92)
        return sheet.size
    finally:sheet.close()
