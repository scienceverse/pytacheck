* Load data;
libname mylib 'C:\data';
/* block
   comment */
proc import datafile='raw.csv' out=d; run;
data x; infile rawdata.dat; input a b; run;
