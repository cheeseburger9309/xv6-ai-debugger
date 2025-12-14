#include "types.h"
#include "stat.h"
#include "user.h"

int main(int argc, char *argv[]) {
  printf(1, "Starting user crash test (Div Zero)...\n");
  
  int x = 10;
  int y = 0;
  int z = 0;

  // Use inline assembly to prevent compiler from optimizing this away
  // equivalent to: z = x / y;
  asm volatile("div %2" 
               : "=a" (z) 
               : "a" (x), "r" (y) 
               : "rdx"); 

  printf(1, "Result: %d\n", z);
  exit();
}

