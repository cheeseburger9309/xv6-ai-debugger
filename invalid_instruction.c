#include "types.h"
#include "stat.h"
#include "user.h"

int main(int argc, char *argv[]) {
  printf(1, "invalid_instruction: Testing invalid opcode...\n");
  
  // Execute invalid/undefined instruction
  asm volatile(".byte 0x0f, 0x0b");  // UD2 instruction (undefined)
  
  printf(1, "After invalid instruction (should not print)\n");
  exit();
}

