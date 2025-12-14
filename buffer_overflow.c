#include "types.h"
#include "stat.h"
#include "user.h"

void vulnerable_function(char *input) {
  char buffer[8];  // Small buffer
  int i;
  
  // Deliberately overflow the buffer
  for (i = 0; i < 20; i++) {
    buffer[i] = input[i];  // Write beyond buffer bounds
  }
}

int main(int argc, char *argv[]) {
  printf(1, "buffer_overflow: Testing buffer overflow...\n");
  
  char large_input[20] = "AAAAAAAAAAAAAAAAAA";
  vulnerable_function(large_input);
  
  printf(1, "Completed (should not print)\n");
  exit();
}

