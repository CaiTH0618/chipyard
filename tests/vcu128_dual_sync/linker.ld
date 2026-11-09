OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { APP (rwx) : ORIGIN = 0x80000000, LENGTH = 1M }
PHDRS { code PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
 . = ORIGIN(APP);
 .text : { KEEP(*(.text.init)) *(.text .text.*) } > APP :code
 .rodata : { KEEP(*(.rodata.build_info)) *(.rodata .rodata.*) *(.srodata .srodata.*) } > APP :code
 .data : { *(.data .data.*) *(.sdata .sdata.*) } > APP :data
 PROVIDE(__global_pointer$ = ADDR(.data) + 0x800);
 .bss (NOLOAD) : {
  . = ALIGN(64); __bss_start = .;
  *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON)
  . = ALIGN(64); __bss_end = .;
 } > APP :data
 __image_end = .;
 _stack0_bottom = 0x80110000; _stack0_top = 0x80120000;
 _stack1_bottom = 0x80130000; _stack1_top = 0x80140000;
 ASSERT(__image_end <= 0x80100000, "APP image exceeds 1 MiB")
 ASSERT((__bss_start % 8)==0 && (__bss_end % 8)==0, "BSS alignment")
 /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
