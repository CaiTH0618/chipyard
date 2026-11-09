"""XDMA transfer code reused from the board-verified ResNet50 Host. CPU/FBus addresses."""
import ctypes
import errno
import mmap
import os
from protocol import APP_BASE, HBM_END

class DMA:
    def __init__(self,h2c,c2h):
        self.h2c,self.c2h=h2c,c2h
        self.libc=ctypes.CDLL(None,use_errno=True)
        for name in ('pread','pwrite'):
            fn=getattr(self.libc,name)
            fn.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int64]
            fn.restype=ctypes.c_ssize_t

    def transfer(self,address,data=None,length=None):
        size=len(data) if data is not None else length
        if size is None or size<=0 or not APP_BASE<=address<address+size<=HBM_END:
            raise ValueError('invalid CPU/FBus DMA range')
        output=bytearray(); position=0
        while position<size:
            count=min(65536,size-position); off=(address+position)%4096
            with mmap.mmap(-1,((count+off+4095)//4096)*4096) as buffer:
                anchor=ctypes.c_char.from_buffer(buffer)
                pointer=ctypes.addressof(anchor)+off
                if data is not None: buffer[off:off+count]=data[position:position+count]
                fn=self.libc.pwrite if data is not None else self.libc.pread
                fd=self.h2c if data is not None else self.c2h
                while True:
                    ret=fn(fd,pointer,count,address+position)
                    if ret<0 and ctypes.get_errno()==errno.EINTR: continue
                    break
                del anchor
                if ret<0:
                    error=ctypes.get_errno(); raise OSError(error,os.strerror(error))
                if ret!=count: raise IOError(f'short XDMA transfer at {address+position:#x}: {ret}/{count}')
                if data is None: output.extend(buffer[off:off+count])
            position+=count
        return bytes(output) if data is None else None

    def read(self,address,size): return self.transfer(address,length=size)
    def write(self,address,data): self.transfer(address,data=data)


def compare(actual,expected,label):
    if actual!=expected:
        i=next((i for i,(a,b) in enumerate(zip(actual,expected)) if a!=b),min(len(actual),len(expected)))
        raise RuntimeError(f'{label}: byte {i} mismatch')

