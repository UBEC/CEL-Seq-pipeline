#!/usr/bin/python2
""" Paired end barcode splitter

Splits one fastq file based on barcodes from another file. The script 
accepts one barcode index file, one sample sheet, and a set of fastq files.
The fastq files are assumed to be read1 (the barcode), and the script replaces
"_R1_" with "_R2_" to find the second file. 

"""

from __future__ import print_function, division

import os, os.path
import argparse
import csv
from logging import getLogger
from itertools import izip
from collections import OrderedDict, namedtuple

from Bio import SeqIO
from HTSeq import FastqReader

FN_SCHEME = "{0.project}_{0.series}_sample_{0.id}.fastq"
FN_UNKNOWN = "undetermined_{0}.fastq"
CUT_LENGTH = 35


logger = getLogger('pijp.bc_demultiplex')
debug, info = logger.debug, logger.info

def main(bc_index_file, sample_sheet, input_files, stats_file, output_dir, min_bc_quality):
    """ this is the main function of this module. Does the splitting 
        and calls any other function.
        fastq_file_list is delimited by newlines, because config parser has no list functionality
    """
    bc_dict = create_bc_dict(bc_index_file)
    sample_dict, sample_counter = create_sample_dict(sample_sheet)
    files_dict = create_output_files(sample_dict, output_dir)

    for fastq_file in input_files:
        r1_file = fastq_file
        r2_file = fastq_file.replace("_R1_", "_R2_")

        # derive lane and il_barcode from filename
        split_name = os.path.basename(fastq_file).split("_")
        il_barcode = split_name[0]
        lane = split_name[2]

        bc_split(bc_dict, sample_dict, sample_counter, files_dict, min_bc_quality, lane, il_barcode, r1_file, r2_file)

        total = sum(sample_counter.values())
        stats = ["# Sample_id\treads\tprecentage\n"]

        for sample_id, sample_count in sample_counter.items():
            stats += ["{0}\t{1}\t{2}\n".format(sample_id, sample_count, 100.0*sample_count/total)]
        stats += ["total\t{0}\t100\n".format(total)]
        with open(os.path.join(output_dir,stats_file), "w") as stat_fh:
            stat_fh.writelines(stats)
    
    for file in files_dict.values():
        file.close()

def create_output_files(sample_dict, target):

    files_dict = dict()
    for sample in set(sample_dict.values()):
        filename = os.path.join(target, FN_SCHEME.format(sample))
        files_dict[sample.id] = open(filename,"wb")
    filename = os.path.join(target, FN_UNKNOWN.format('R1'))
    files_dict['unknown_bc_R1'] = open(filename, "wb")
    filename = os.path.join(target, FN_UNKNOWN.format('R2'))
    files_dict['unknown_bc_R2'] = open(filename, "wb")
    return files_dict
    
def create_sample_dict(sample_sheet_file):
    sample_dict = OrderedDict()
    sample_counter = OrderedDict()
    Key = namedtuple('sample_key',['flocell', 'lane', 'il_barcode', 'cel_barcode'])
    Sample_info = namedtuple('Sample', ['id', 'series', 'project'])
    with open(sample_sheet_file, 'rb') as sample_sheet_fh:
        sample_sheet_reader = csv.DictReader(sample_sheet_fh, delimiter='\t')
        for row in sample_sheet_reader:
            id = "{0:04}".format(int(row["#id"]))  #  id has an extra "#" becaues its the first field
            key = Key(row["flocell"], row["lane"], row["il_barcode"], row["cel_barcode"])
            sample_dict[key] = Sample_info(id, row["series"], row["project"])
            sample_counter[id] = 0
    sample_counter['undetermined'] = 0
    sample_counter['unqualified'] = 0
    return sample_dict, sample_counter
                                  

def create_bc_dict(bc_index_file):
    bc_dict = dict()
    with open(bc_index_file, 'rb') as bc_index:
        bc_index_reader = csv.reader(bc_index, delimiter='\t')
        for row in bc_index_reader:
            if row[0].startswith("#"):
                continue
            bc_dict[row[1]] = row[0]
    return bc_dict

def bc_split(bc_dict, sample_dict, sample_counter, files_dict, min_bc_quality, lane, il_barcode, r1_file, r2_file):
    """ Splits two fastq files according to barcode """
    #r1 = SeqIO.parse(r1_file, "fastq-sanger")
    #r2 = SeqIO.parse(r2_file, "fastq-sanger")
    r1 = FastqReader(r1_file)
    r2 = FastqReader(r2_file)
    for n, (read1, read2) in enumerate(izip(r1,r2)):
        if (n % 1e5)==0:
            info("read number {0}".format(n))
        # validate reads are the same
        #debug("read : {}".format(read1))
        assert (read1.name.split()[0] == read2.name.split()[0]), "Reads have different ids. Aborting."
        # check quality:
        quals = read1.qual[0:8]
        if min(quals) >= int(min_bc_quality):
            # find and split
            barcode = str(read1.seq[0:8])
            cel_bc_id = bc_dict.get(barcode, None)
            flocell = read1.name.split(":")[2]
            sample = sample_dict.get((flocell, lane, il_barcode, cel_bc_id),None)
            #debug("cel-seq barcode id = {0}".format(cel_bc_id))
            #debug("sample is {0}".format(sample))
            if (cel_bc_id is not None) and (sample is not None):
                fh = files_dict[sample.id]
                (read2[:CUT_LENGTH]).write_to_fastq_file(fh)
                sample_counter[sample.id] += 1
            else:
                fh1 = files_dict['unknown_bc_R1']
                fh2 = files_dict['unknown_bc_R2']
                (read1).write_to_fastq_file( fh1)
                (read2[:CUT_LENGTH]).write_to_fastq_file( fh2)
                sample_counter['undetermined'] += 1
        else:
            sample_counter['unqualified'] +=1
    return sample_counter
                
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-bc-quality', metavar='N', type=int, default=10, 
                        help='Minimal quality for barcode reads (default=10)')
    parser.add_argument('--out-dir', metavar='DIRNAME', type=str, default='.',
                        help='Output directory. Defaults to current directory')
    parser.add_argument('--stats-file', metavar='STATFILE', type=str, default='stats.tab',
                        help='Statistics file name (default: stats.tab)')
    parser.add_argument('bc_index', type=str)
    parser.add_argument('sample_sheet', type=str)
    parser.add_argument('fastq_files', type=str, nargs='+')
    args = parser.parse_args()
    main(args.bc_index, args.sample_sheet, args.fastq_files, stats_file=args.stats_file,
         output_dir=args.out_dir, min_bc_quality=args.min_bc_quality)
