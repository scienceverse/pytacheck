/* Fit the model */
PROC IMPORT DATAFILE="C:\Users\lisa\data.xlsx" OUT=work.dat DBMS=xlsx;
RUN;
* a comment line;
PROC MEANS DATA=work.dat;
RUN;
