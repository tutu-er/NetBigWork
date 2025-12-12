A=4096*0.5/3.3-1;%信号幅值
N=200;%一周期内数据点数
Ph=0;%初始相位
SineData=ceil(A*sin(Ph:2*pi/N:2*pi*(1-1/N)+Ph)+1.25*4096/3.3-1);
Fid = fopen('SineWaveData.txt','w');
fprintf(Fid,'%d,',SineData);
fclose(Fid);