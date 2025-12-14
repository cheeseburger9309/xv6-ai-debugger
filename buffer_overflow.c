#include "types.h"
#include "stat.h"
#include "user.h"

void vulnerable_function(char *input) {
  char buffer[8];  // Small buffer
  int i;
  
  // Deliberately overflow the buffer - write way beyond bounds
  for (i = 0; i < 100; i++) {
    buffer[i] = input[i % 20];  // This will corrupt the stack
  }
  
  // Try to use the corrupted buffer to prevent optimization
  printf(1, "Buffer first char: %c\n", buffer[0]);
}

int main(int argc, char *argv[]) {
  printf(1, "buffer_overflow: Testing buffer overflow...\n");
  
  char large_input[20] = "AAAABBBBCCCCDDDDEEEE";
  vulnerable_function(large_input);
  
  printf(1, "Completed (should not print)\n");
  exit();
}

