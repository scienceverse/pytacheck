proc import datafile='low.csv' out=a dbms=csv;
run;
PROC IMPORT DATAFILE='UP.csv' OUT=b DBMS=CSV;
RUN;
