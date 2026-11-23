.section .rodata.qops_blob,"a",@progbits
.balign 64
.globl qops_blob
qops_blob:
.incbin "generated/qops_blob.bin"
.globl qops_blob_end
qops_blob_end:
.section .note.GNU-stack,"",@progbits
